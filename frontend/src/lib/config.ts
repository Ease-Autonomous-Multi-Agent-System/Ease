/**
 * Where the browser reaches the Ease API. 127.0.0.1 (not "localhost") avoids a slow IPv6 attempt on Windows.
 * A path such as "/backend" means "same origin, behind the gateway" (the deployed setup, see deploy/gateway.conf).
 */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

/** WebSocket base URL; for a same-origin API it is derived from the page's own address at call time. */
export function wsUrl(): string {
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) return explicit.replace(/\/$/, "");
  if (API_URL.startsWith("/")) {
    return `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}${API_URL}`;
  }
  return API_URL.replace(/^http/, "ws");
}

/** Absolute URL for a signed artifact path the API returns (e.g. "/files/...?exp=..&sig=.."). */
export function fileUrl(signed: string | null | undefined): string | null {
  if (!signed) return null;
  return signed.startsWith("/") ? `${API_URL}${signed}` : null;
}

/** Only ever render http(s) links from agent output - never javascript:, data: etc. */
export function safeHref(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}
