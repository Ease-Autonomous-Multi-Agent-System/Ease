"""Outbound network policy for the browser and API agents.

 * SSRF: an LLM-chosen or page-supplied URL must not reach the Docker network, cloud metadata endpoints or the
   user's LAN. Only http(s) to public addresses is allowed, plus the explicitly allowlisted fixture hosts.
 * robots.txt is respected for browser navigation (ethics section of the paper), cached per origin.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.robotparser
from functools import lru_cache
from urllib.parse import urlsplit

import httpx

from ease.config import get_settings

USER_AGENT = "EaseAgent/0.1 (+academic research project; supervised automation)"
_ALLOWED_PUBLIC_PORTS = {None, 80, 443}
_BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata", "host.docker.internal", "postgres", "redis", "api"}


class BlockedURL(Exception):
    pass


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return addr.is_global and not addr.is_multicast


@lru_cache(maxsize=512)
def _resolve(host: str) -> tuple[str, ...]:
    try:
        return tuple({info[4][0] for info in socket.getaddrinfo(host, None)})
    except socket.gaierror as exc:
        raise BlockedURL(f"cannot resolve host {host!r}") from exc


def check_url(url: str) -> str:
    """Return the URL if it may be fetched, else raise BlockedURL."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise BlockedURL(f"scheme {parts.scheme!r} is not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise BlockedURL("URL has no host")
    if parts.username or parts.password:
        raise BlockedURL("credentials in URLs are not allowed")
    if host in get_settings().fixture_host_set:
        ports = get_settings().fixture_port_set
        if ports and (parts.port or (443 if parts.scheme == "https" else 80)) not in ports:
            raise BlockedURL(f"port {parts.port} is not allowed on {host!r}")
        return url
    if host in _BLOCKED_HOSTNAMES or host.endswith(".internal") or host.endswith(".local"):
        raise BlockedURL(f"host {host!r} is internal")
    if parts.port not in _ALLOWED_PUBLIC_PORTS:
        raise BlockedURL(f"port {parts.port} is not allowed for public hosts")
    for ip in _resolve(host):
        if not _is_public(ip):
            raise BlockedURL(f"host {host!r} resolves to non-public address")
    return url


def is_fixture(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in get_settings().fixture_host_set


_robots_cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}


def robots_allowed(url: str) -> bool:
    if not get_settings().respect_robots_txt or is_fixture(url):
        return True
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    cached = _robots_cache.get(origin)
    if cached is None or time.time() - cached[0] > 3600:
        parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
        try:
            resp = httpx.get(f"{origin}/robots.txt", timeout=5, headers={"User-Agent": USER_AGENT})
            if resp.status_code >= 400:
                parser = None  # no robots.txt -> allowed
            else:
                parser.parse(resp.text.splitlines())
        except httpx.HTTPError:
            parser = None
        _robots_cache[origin] = (time.time(), parser)
        cached = _robots_cache[origin]
    parser = cached[1]
    return True if parser is None else parser.can_fetch(USER_AGENT, url)
