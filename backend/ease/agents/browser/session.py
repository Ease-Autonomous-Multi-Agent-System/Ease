"""Playwright browser lifecycle with the network policy enforced on every request."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from ease.agents.browser.dom import INDEX_JS, Observation
from ease.config import get_settings
from ease.logs import log
from ease.schemas.contracts import ArtifactRef
from ease.security.netguard import BlockedURL, check_url, robots_allowed

VIEWPORT = {"width": 1280, "height": 800}
_BOT_WALL = re.compile(
    r"verify you are (a )?human|are you a robot|unusual traffic|access denied|attention required|"
    r"just a moment\.\.\.|checking your browser|press (and|&) hold|captcha",
    re.I,
)


class ArtifactWriter:
    """Screenshots/HTML dumps go to disk; only their relative path enters graph state (keeps checkpoints small)."""

    def __init__(self, root: Path, task_id: str, step_key: str):
        self.root = root
        self.rel = Path(task_id) / step_key
        (root / self.rel).mkdir(parents=True, exist_ok=True)
        # continue numbering: a resumed step (possibly another worker) must never overwrite earlier evidence
        self.n = len(list((root / self.rel).iterdir()))

    def write(self, name: str, data: bytes, kind: str = "screenshot") -> ArtifactRef:
        self.n += 1
        ext = "png" if kind == "screenshot" else "html" if kind == "html" else "json"
        rel = self.rel / f"{self.n:03d}-{re.sub(r'[^a-z0-9_-]', '', name.lower())[:40]}.{ext}"
        (self.root / rel).write_bytes(data)
        return ArtifactRef(kind=kind, uri=rel.as_posix(), bytes=len(data))


def base_domain(host: str) -> str:
    parts = host.lower().split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "ac", "org", "net", "gov", "edu"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


class BrowserSession:
    def __init__(self, artifacts: ArtifactWriter, *, start_url: str, cookies: list[dict[str, Any]] | None = None):
        self.artifacts = artifacts
        self.start_url = start_url
        self.allowed_domain = base_domain(urlsplit(start_url).hostname or "")
        self.cookies = cookies or []
        self.blocked: list[str] = []
        self._pw = self._browser = self._context = self.page = None

    # -------- lifecycle --------
    def __enter__(self) -> BrowserSession:
        from playwright.sync_api import sync_playwright

        s = get_settings()
        self._pw = sync_playwright().start()
        if s.browser_cdp_url:  # host mode: drive the user's real Chrome
            self._browser = self._pw.chromium.connect_over_cdp(s.browser_cdp_url)
            self._context = self._browser.new_context(viewport=VIEWPORT)
        else:
            self._browser = self._pw.chromium.launch(
                headless=s.browser_headless, args=["--disable-dev-shm-usage", "--no-first-run"]
            )
            self._context = self._browser.new_context(
                viewport=VIEWPORT, accept_downloads=False, service_workers="block", locale="en-US",
                timezone_id="Asia/Kolkata",
            )
        self._context.set_default_timeout(15000)
        self._context.route("**/*", self._guard)
        if self.cookies:
            self._context.add_cookies(self.cookies)
        self.page = self._context.new_page()
        self.page.on("dialog", lambda d: d.dismiss())  # alert/confirm/prompt never block the agent
        return self

    def __exit__(self, *exc: Any) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:  # noqa: S112 - best-effort cleanup; the worker must not leak Chromium
                continue
        if self._pw is not None:
            self._pw.stop()

    def _guard(self, route: Any, request: Any) -> None:
        url = request.url
        if url.startswith(("data:", "blob:")):
            route.continue_()
            return
        try:
            check_url(url)
        except BlockedURL as exc:
            self.blocked.append(f"{url[:100]} ({exc})")
            route.abort("blockedbyclient")
            return
        route.continue_()

    # -------- navigation --------
    def resolve(self, href: str) -> str:
        return urljoin(self.page.url if self.page.url.startswith("http") else self.start_url, href)

    def allowed_navigation(self, url: str) -> str | None:
        """Return a reason if the agent may not navigate here. Navigation is pinned to the start site's domain so
        injected page text can't send the agent (and the user's data) somewhere else."""
        host = (urlsplit(url).hostname or "").lower()
        if base_domain(host) != self.allowed_domain:
            return f"navigation outside {self.allowed_domain} is not allowed for this step"
        try:
            check_url(url)
        except BlockedURL as exc:
            return str(exc)
        if not robots_allowed(url):
            return "disallowed by the site's robots.txt"
        return None

    def goto(self, url: str) -> None:
        reason = self.allowed_navigation(url) if self.page.url.startswith("http") else None
        if reason is None:
            check_url(url)
            if not robots_allowed(url):
                raise BlockedURL("disallowed by robots.txt")
        else:
            raise BlockedURL(reason)
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self.settle()

    def settle(self, timeout_ms: int = 2500) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:  # noqa: S110 - long-polling pages never go idle; that's fine
            pass
        time.sleep(0.2)

    # -------- observation --------
    def observe(self) -> Observation:
        return Observation.from_js(self.page.evaluate(INDEX_JS))

    def bot_wall(self, obs: Observation) -> bool:
        if _BOT_WALL.search(obs.title) or _BOT_WALL.search(obs.text[:1500]):
            return True
        return any(re.search(r"recaptcha|hcaptcha|turnstile|captcha", f.url or "", re.I) for f in self.page.frames)

    def login_wall(self, obs: Observation) -> bool:
        has_password = any(e.type == "password" for e in obs.elements)
        return has_password and len(obs.text) < 600

    def screenshot(self, name: str, full_page: bool = False) -> ArtifactRef:
        from ease.agents.browser.som import downscale

        png = self.page.screenshot(type="png", full_page=full_page)
        return self.artifacts.write(name, downscale(png, 1280))

    def el(self, eid: int):
        return self.page.locator(f'[data-ease-id="{eid}"]').first


def log_blocked(session: BrowserSession) -> None:
    if session.blocked:
        log.info("browser.blocked_requests", count=len(session.blocked), sample=session.blocked[:3])
