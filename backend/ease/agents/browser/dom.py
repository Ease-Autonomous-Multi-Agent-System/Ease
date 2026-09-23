"""Element index - the primary grounding signal (DOM + accessibility tree).

A JS snippet walks the live page, keeps visible interactive elements, tags each with data-ease-id="N" and returns
a compact description (role, accessible name, value, ...) plus a stable locator for replay. The index is
re-derived after every action, so ids never go stale across re-renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

INDEX_JS = r"""
() => {
  const MAX = 150;
  const q = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=tab],[role=checkbox],' +
            '[role=menuitem],[role=option],[role=switch],[contenteditable=true],[onclick],[tabindex]:not([tabindex="-1"])';
  document.querySelectorAll('[data-ease-id]').forEach(e => e.removeAttribute('data-ease-id'));
  const vis = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0.05;
  };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const labelOf = el => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const lb = el.getAttribute('aria-labelledby');
    if (lb) return lb.split(' ').map(id => document.getElementById(id)?.innerText || '').join(' ');
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText; }
    const wrap = el.closest('label'); if (wrap) return wrap.innerText;
    return '';
  };
  const nameOf = el => {
    const tag = el.tagName.toLowerCase();
    if (['input', 'select', 'textarea'].includes(tag))
      return clean(labelOf(el) || el.getAttribute('placeholder') || el.getAttribute('title') || el.name);
    return clean(labelOf(el) || el.innerText || el.getAttribute('title') || el.getAttribute('alt') ||
                 el.querySelector('img[alt]')?.getAttribute('alt') || '');
  };
  const locator = el => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const tag = el.tagName.toLowerCase();
    if (el.name && ['input', 'select', 'textarea', 'button'].includes(tag)) {
      const sel = `${tag}[name="${el.name}"]`;
      if (document.querySelectorAll(sel).length === 1) return sel;
    }
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n !== document.body; n = n.parentElement) {
      let p = n.tagName.toLowerCase();
      if (n.id) { parts.unshift('#' + CSS.escape(n.id)); break; }
      const sib = [...(n.parentElement?.children || [])].filter(c => c.tagName === n.tagName);
      if (sib.length > 1) p += `:nth-of-type(${sib.indexOf(n) + 1})`;
      parts.unshift(p);
    }
    return parts.join(' > ');
  };
  const out = []; let i = 0;
  for (const el of document.querySelectorAll(q)) {
    if (out.length >= MAX) break;
    if (!vis(el) || el.closest('[aria-hidden=true]') || el.disabled) continue;
    const r = el.getBoundingClientRect(), tag = el.tagName.toLowerCase();
    el.setAttribute('data-ease-id', String(i));
    const form = el.form || el.closest('form');
    // A <button> reports type "submit" even outside any form, so only count it as a form submit when it
    // actually belongs to a form and its declared type is submit (missing type attribute = submit).
    const btype = (el.getAttribute('type') || 'submit').toLowerCase();
    const submits = !!form && ((tag === 'button' && btype === 'submit') ||
                               (tag === 'input' && (el.type === 'submit' || el.type === 'image')));
    out.push({
      id: i++, tag, role: el.getAttribute('role') || '', type: (el.getAttribute('type') || '').toLowerCase(),
      name: nameOf(el).slice(0, 120), value: ['input', 'textarea', 'select'].includes(tag) && el.type !== 'password' ? String(el.value || '').slice(0, 80) : '',
      checked: el.type === 'checkbox' || el.type === 'radio' ? !!el.checked : null,
      required: !!el.required, href: tag === 'a' ? el.getAttribute('href') : null,
      options: tag === 'select' ? [...el.options].map(o => clean(o.text)).slice(0, 25) : null,
      in_form: !!form, form_submit: submits,
      in_viewport: r.bottom > 0 && r.top < innerHeight, box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      locator: locator(el), sensitive: el.type === 'password' || /card|cvv|iban|otp|ssn|aadhaar|pan/i.test(el.name + ' ' + el.id + ' ' + el.autocomplete),
    });
  }
  const main = document.querySelector('main') || document.body;
  return {
    url: location.href, title: document.title, elements: out,
    text: clean(main.innerText).slice(0, 7000),
    scroll: { y: Math.round(scrollY), max: Math.max(0, document.documentElement.scrollHeight - innerHeight) },
  };
}
"""


@dataclass
class Element:
    id: int
    tag: str
    role: str
    type: str
    name: str
    value: str
    checked: bool | None
    required: bool
    href: str | None
    options: list[str] | None
    in_form: bool
    form_submit: bool
    in_viewport: bool
    box: list[int]
    locator: str
    sensitive: bool

    def kind(self) -> str:
        if self.role:
            return self.role
        if self.tag == "input":
            return f"input[{self.type or 'text'}]"
        return self.tag

    def describe(self) -> str:
        bits = [f"[{self.id}] {self.kind()}"]
        bits.append(f'"{self.name}"' if self.name else "(no label)")
        if self.value:
            bits.append(f"value={self.value!r}")
        if self.checked is not None:
            bits.append("checked" if self.checked else "unchecked")
        if self.required:
            bits.append("required")
        if self.options:
            bits.append("options=" + "|".join(self.options[:12]))
        if self.href and not self.href.startswith("javascript"):
            bits.append(f"-> {self.href[:80]}")
        if self.form_submit:
            bits.append("SUBMITS-FORM")
        if not self.in_viewport:
            bits.append("(offscreen)")
        return " ".join(bits)


@dataclass
class Observation:
    url: str
    title: str
    elements: list[Element]
    text: str
    scroll: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_js(cls, data: dict[str, Any]) -> Observation:
        return cls(data["url"], data["title"], [Element(**e) for e in data["elements"]], data["text"],
                   data.get("scroll", {}))

    def element(self, eid: int) -> Element | None:
        return next((e for e in self.elements if e.id == eid), None)

    def ambiguous(self) -> bool:
        """When the text index alone is a poor guide (icon-only controls). In hybrid mode this, a click with no
        visible effect, or the model asking to "look" is what triggers attaching a Set-of-Marks screenshot."""
        # Repeated labels ("Add to cart" on every card) are normal and each carries its own id, so they don't
        # trigger vision on their own; unlabelled controls do.
        return any(not e.name and e.tag in ("button", "a") and e.in_viewport for e in self.elements)

    def signature(self) -> str:
        return f"{self.url}|{len(self.elements)}|{hash(self.text[:2000])}"

    def render(self, max_elements: int = 120, with_names: bool = True) -> str:
        lines = [f"URL: {self.url}", f"TITLE: {self.title}",
                 f"SCROLL: {self.scroll.get('y', 0)}/{self.scroll.get('max', 0)}", "INTERACTIVE ELEMENTS:"]
        for e in self.elements[:max_elements]:
            lines.append("  " + (e.describe() if with_names else f"[{e.id}] {e.kind()}"))
        return "\n".join(lines)
