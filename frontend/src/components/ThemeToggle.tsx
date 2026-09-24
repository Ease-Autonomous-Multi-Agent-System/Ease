"use client";

import { IconDeviceDesktop, IconMoon, IconSun } from "@tabler/icons-react";
import { useSyncExternalStore } from "react";

type Mode = "system" | "light" | "dark";
const NEXT: Record<Mode, Mode> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<Mode, string> = { system: "Theme: system", light: "Theme: light", dark: "Theme: dark" };
const listeners = new Set<() => void>();

// The theme lives on <html data-theme>; this is a tiny external store over it (server render = "system").
function read(): Mode {
  const v = document.documentElement.getAttribute("data-theme");
  return v === "light" || v === "dark" ? v : "system";
}
function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function ThemeToggle() {
  const mode = useSyncExternalStore(subscribe, read, () => "system" as Mode);

  function cycle() {
    const next = NEXT[mode];
    const root = document.documentElement;
    if (next === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", next);
    try {
      if (next === "system") localStorage.removeItem("ease-theme");
      else localStorage.setItem("ease-theme", next);
    } catch {
      /* private mode: the choice just isn't remembered */
    }
    listeners.forEach((l) => l());
  }

  const Icon = mode === "light" ? IconSun : mode === "dark" ? IconMoon : IconDeviceDesktop;
  return (
    <button className="btn btn-quiet icon-btn" onClick={cycle} aria-label={LABEL[mode]} title={LABEL[mode]}>
      <Icon size={18} aria-hidden="true" />
    </button>
  );
}
