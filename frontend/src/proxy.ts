import { NextRequest, NextResponse } from "next/server";

/**
 * Per-request Content-Security-Policy with a nonce (Next.js 16 "proxy" convention, formerly middleware).
 * Only our own scripts run (nonce + strict-dynamic); the page may talk to the Ease API (HTTP + WebSocket) and
 * nothing else. Styles allow 'unsafe-inline' because Next.js and React inject <style> tags without a nonce;
 * script execution - the part that matters for XSS - stays nonce-only.
 */
export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const isDev = process.env.NODE_ENV === "development";
  const rawApi = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
  // a same-origin API ("/backend") is already covered by 'self' (CSP3 'self' includes ws/wss to the same host)
  const api = rawApi.startsWith("/") ? "" : rawApi;
  const ws = rawApi.startsWith("/") ? "" : (process.env.NEXT_PUBLIC_WS_URL || api.replace(/^http/, "ws")).replace(/\/$/, "");
  const https = rawApi.startsWith("https") || request.headers.get("x-forwarded-proto") === "https";
  const csp = `
    default-src 'self';
    script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""};
    style-src 'self' 'unsafe-inline';
    img-src 'self' blob: data: ${api};
    font-src 'self';
    connect-src 'self' ${api} ${ws}${isDev ? " ws://localhost:* ws://127.0.0.1:*" : ""};
    object-src 'none';
    base-uri 'self';
    form-action 'self';
    frame-ancestors 'none';
    ${https ? "upgrade-insecure-requests;" : ""}
  `.replace(/\s{2,}/g, " ").trim();

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "no-referrer");
  response.headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!api|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
