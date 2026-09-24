/** Where the browser reaches the Ease API. 127.0.0.1 (not "localhost") avoids a slow IPv6 attempt on Windows. */
export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
export const WS_URL = (process.env.NEXT_PUBLIC_WS_URL || API_URL.replace(/^http/, "ws")).replace(/\/$/, "");

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
