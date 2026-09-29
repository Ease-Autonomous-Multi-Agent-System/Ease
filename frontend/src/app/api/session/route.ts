import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

/**
 * Session endpoint (backend-for-frontend). The long-lived refresh token lives ONLY in an httpOnly, SameSite=Strict
 * cookie scoped to this path - page JavaScript can never read it, so an XSS bug can't steal a login. The browser
 * gets just the 15-minute access token, which it keeps in memory.
 */
const BACKEND = (process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const COOKIE = "ease_rt";
const MAX_AGE = 7 * 24 * 3600;

type Body = { action?: string; email?: string; password?: string; full_name?: string; invite_code?: string };

function sameOrigin(req: NextRequest): boolean {
  // CSRF defence on top of SameSite=Strict: only our own pages may call this.
  // Compared with the Host the browser sent: behind Docker or a proxy, req.nextUrl carries the server's own bind
  // address (e.g. 0.0.0.0:3000), not the address the user opened.
  const origin = req.headers.get("origin");
  const host = req.headers.get("host");
  if (!origin || !host) return false;
  try {
    const o = new URL(origin);
    return (o.protocol === "http:" || o.protocol === "https:") && o.host === host;
  } catch {
    return false;
  }
}

/**
 * The visitor's IP for the API's per-IP limits. This route calls the API from the server, so without it every
 * sign-in would look like it came from the web server itself. The value is the one the gateway set (it
 * overwrites whatever the client sent); the API only trusts it from inside the deployment (--forwarded-allow-ips).
 */
function clientIp(req: NextRequest): string | null {
  const ip = (req.headers.get("x-forwarded-for") || "").split(",").pop()?.trim() || "";
  return /^[0-9a-f.:]{3,45}$/i.test(ip) ? ip : null;
}

async function backend(path: string, payload: unknown, ip: string | null) {
  const res = await fetch(`${BACKEND}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(ip ? { "X-Forwarded-For": ip } : {}) },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

async function setSession(req: NextRequest, data: { access_token: string; refresh_token: string; expires_in: number }) {
  (await cookies()).set(COOKIE, data.refresh_token, {
    httpOnly: true,
    sameSite: "strict",
    // behind the gateway/tunnel the app itself speaks http; the forwarded protocol says what the browser used
    secure: req.nextUrl.protocol === "https:" || req.headers.get("x-forwarded-proto") === "https",
    path: "/api/session",
    maxAge: MAX_AGE,
  });
  return NextResponse.json({ access_token: data.access_token, expires_in: data.expires_in });
}

function fail(status: number, data: { detail?: unknown }) {
  const detail = typeof data?.detail === "string" ? data.detail : "Something went wrong. Try again.";
  return NextResponse.json({ detail }, { status });
}

export async function POST(req: NextRequest) {
  if (!sameOrigin(req) || !req.headers.get("content-type")?.includes("application/json")) {
    return NextResponse.json({ detail: "forbidden" }, { status: 403 });
  }
  const body = (await req.json().catch(() => ({}))) as Body;
  const jar = await cookies();
  const ip = clientIp(req);

  switch (body.action) {
    case "login": {
      const r = await backend("/auth/login", { email: body.email, password: body.password }, ip);
      return r.ok ? setSession(req, r.data) : fail(r.status, r.data);
    }
    case "register": {
      const r = await backend("/auth/register", {
        email: body.email, password: body.password, full_name: body.full_name ?? "",
        invite_code: body.invite_code || undefined,
      }, ip);
      if (!r.ok && r.status === 422 && Array.isArray(r.data?.detail)) {
        return NextResponse.json({ detail: "Use a valid email and a password of at least 10 characters with letters and digits." }, { status: 422 });
      }
      return r.ok ? setSession(req, r.data) : fail(r.status, r.data);
    }
    case "refresh": {
      const rt = jar.get(COOKIE)?.value;
      if (!rt) return NextResponse.json({ detail: "signed out" }, { status: 401 });
      const r = await backend("/auth/refresh", { refresh_token: rt }, ip);
      if (!r.ok) {
        jar.delete({ name: COOKIE, path: "/api/session" });
        return fail(401, r.data);
      }
      return setSession(req, r.data);
    }
    case "logout": {
      const rt = jar.get(COOKIE)?.value;
      if (rt) await backend("/auth/logout", { refresh_token: rt }, ip).catch(() => null);
      jar.delete({ name: COOKIE, path: "/api/session" });
      return NextResponse.json({ ok: true });
    }
    default:
      return NextResponse.json({ detail: "unknown action" }, { status: 400 });
  }
}
