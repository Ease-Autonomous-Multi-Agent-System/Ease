"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

/** Cloudflare Turnstile, the free human check on sign-up. Rendered only when the deployment configures it. */
type TurnstileApi = {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  reset: (id: string) => void;
  remove: (id: string) => void;
};
declare global {
  interface Window { turnstile?: TurnstileApi }
}

const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
let loading: Promise<void> | null = null;

function loadScript(): Promise<void> {
  if (window.turnstile) return Promise.resolve();
  loading ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => { loading = null; reject(new Error("Couldn't load the human check")); };
    document.head.appendChild(s);
  });
  return loading;
}

export type TurnstileHandle = { reset: () => void };

export const Turnstile = forwardRef<TurnstileHandle, { siteKey: string; onToken: (token: string | null) => void }>(
  function Turnstile({ siteKey, onToken }, ref) {
    const box = useRef<HTMLDivElement>(null);
    const id = useRef<string | null>(null);
    const cb = useRef(onToken);
    useEffect(() => { cb.current = onToken; }, [onToken]);

    useImperativeHandle(ref, () => ({
      // a token works once: after a failed sign-up the widget must issue a new one
      reset: () => { if (id.current && window.turnstile) { window.turnstile.reset(id.current); cb.current(null); } },
    }), []);

    useEffect(() => {
      let alive = true;
      loadScript().then(() => {
        if (!alive || !box.current || !window.turnstile) return;
        id.current = window.turnstile.render(box.current, {
          sitekey: siteKey,
          theme: "auto",
          callback: (t: string) => cb.current(t),
          "expired-callback": () => cb.current(null),
          "error-callback": () => cb.current(null),
        });
      }).catch(() => cb.current(null));
      return () => {
        alive = false;
        if (id.current && window.turnstile) window.turnstile.remove(id.current);
        id.current = null;
      };
    }, [siteKey]);

    return <div ref={box} style={{ minHeight: 65 }} />;
  },
);
