"""Set-of-Marks visual layer - the disambiguation signal, not the primary grounding source.

Draws a numbered box over every indexed element (same ids as the DOM index), takes a screenshot, removes the
overlay, and downscales to ~1024px wide to keep vision-token cost down.
"""

from __future__ import annotations

import io

from PIL import Image

OVERLAY_JS = r"""
(ids) => {
  const layer = document.createElement('div');
  layer.id = '__ease_som';
  layer.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647';
  const colors = ['#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4', '#008080', '#9a6324', '#800000'];
  for (const el of document.querySelectorAll('[data-ease-id]')) {
    const id = el.getAttribute('data-ease-id');
    if (ids && !ids.includes(Number(id))) continue;
    const r = el.getBoundingClientRect();
    if (r.bottom < 0 || r.top > innerHeight || r.width < 2) continue;
    const c = colors[Number(id) % colors.length];
    const box = document.createElement('div');
    box.style.cssText = `position:fixed;left:${r.x}px;top:${r.y}px;width:${r.width}px;height:${r.height}px;border:2px solid ${c}`;
    const tag = document.createElement('div');
    tag.textContent = id;
    tag.style.cssText = `position:absolute;left:-2px;top:-16px;background:${c};color:#fff;font:bold 11px/14px sans-serif;padding:0 3px`;
    box.appendChild(tag); layer.appendChild(box);
  }
  document.documentElement.appendChild(layer);
}
"""
REMOVE_JS = "() => document.getElementById('__ease_som')?.remove()"

MAX_WIDTH = 1024


def downscale(png: bytes, max_width: int = MAX_WIDTH) -> bytes:
    img = Image.open(io.BytesIO(png))
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def som_screenshot(page, ids: list[int] | None = None) -> bytes:
    """Annotated screenshot of the viewport. `page` is a Playwright sync Page."""
    page.evaluate(OVERLAY_JS, ids)
    try:
        png = page.screenshot(type="png")
    finally:
        page.evaluate(REMOVE_JS)
    return downscale(png)
