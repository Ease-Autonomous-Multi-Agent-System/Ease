"""Document ingestion: file -> clean text -> chunks -> embeddings, and resume -> structured profile.

Embeddings are computed locally (fastembed, BAAI/bge-base-en-v1.5, 768-d): free, offline and deterministic,
which the evaluation needs.
"""

from __future__ import annotations

import io
import re
import threading
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field

from ease.llm.router import LlmRouter

EMBED_MODEL = "BAAI/bge-base-en-v1.5"
_lock = threading.Lock()
MAGIC = {b"%PDF": "pdf"}


class UnsupportedDocument(Exception):
    pass


def sniff_type(data: bytes, filename: str) -> str:
    """Decide by content, not by the client-supplied name/MIME (uploads are untrusted)."""
    if data[:4] == b"%PDF":
        return "pdf"
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedDocument("only PDF or UTF-8 text files are accepted") from exc
    if filename.lower().endswith((".txt", ".md")):
        return "text"
    raise UnsupportedDocument("only .pdf, .txt or .md files are accepted")


def extract_text(data: bytes, kind: str) -> str:
    if kind == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UnsupportedDocument("encrypted PDFs are not supported")
        if len(reader.pages) > 30:
            raise UnsupportedDocument("PDF has too many pages (max 30)")
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    else:
        text = data.decode("utf-8", errors="replace")
    text = text.replace(" ", " ").replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_text(text: str, target: int = 700, overlap: int = 120) -> list[str]:
    """Greedy line-packing into ~target-char chunks with a small overlap (keeps section context)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    chunks: list[str] = []
    cur = ""
    for ln in lines:
        if cur and len(cur) + len(ln) + 1 > target:
            chunks.append(cur)
            cur = cur[-overlap:] + " " + ln
        else:
            cur = f"{cur} {ln}".strip()
    if cur:
        chunks.append(cur)
    return chunks


@lru_cache
def _model():
    from fastembed import TextEmbedding

    from ease.config import get_settings

    # Cached in the shared data volume: downloaded once, survives container restarts and image rebuilds.
    cache = get_settings().artifacts_dir.parent / "models"
    cache.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(EMBED_MODEL, cache_dir=str(cache))


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    with _lock:  # the ONNX session is not guaranteed thread-safe
        return [v.tolist() for v in _model().embed(texts, batch_size=16)]


class Profile(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)  # models often return 2027 instead of "2027"

    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    college: str = ""
    degree: str = ""
    graduation_year: str = ""
    skills: list[str] = Field(default_factory=list)
    experience: list[str] = Field(default_factory=list, description="one line per role")
    projects: list[str] = Field(default_factory=list, description="one line per project")
    summary: str = Field(default="", description="2-3 sentence professional summary in first person")


def extract_profile(text: str, router: LlmRouter, *, user_id: str | None = None) -> Profile:
    msg = (
        "Extract the candidate's profile from this resume as JSON with keys: full_name, first_name, last_name, "
        "email, phone, location, linkedin, github, portfolio, college, degree, graduation_year, skills (list), "
        "experience (list of one-line strings), projects (list of one-line strings), summary. Use full URLs "
        "(https://...) for links. Use \"\" when unknown - never invent values.\n\n<resume>\n"
        + text[:9000] + "\n</resume>"
    )
    res = router.complete([{"role": "user", "content": msg}], tier="strong", schema=Profile, user_id=user_id,
                          max_tokens=2000, purpose="profile")
    p: Profile = res.parsed
    for attr in ("linkedin", "github", "portfolio"):
        v = getattr(p, attr)
        if v and not v.startswith("http"):
            setattr(p, attr, "https://" + v.lstrip("/"))
    return p
