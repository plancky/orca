// Build-time API origin. A static SPA has no server runtime, so the backend
// origin is baked in at build via Vite's VITE_* mechanism (never a secret — the
// JWT is obtained at runtime via login). The OpenAPI paths already include
// `/api/v1`, so this is the ORIGIN only (e.g. http://localhost:8000).
const url = import.meta.env.VITE_API_BASE_URL;
if (!url) {
  throw new Error("VITE_API_BASE_URL is required at build time");
}

export const API_BASE_URL: string = url;

/**
 * WebSocket URL for the documented progressive-enhancement follow-up
 * (`/ws/query?token=<jwt>`). Poll is the v1 correctness floor; this helper is
 * exported for the follow-up and unused in v1.
 */
export function wsUrl(path = "/ws/query"): string {
  const u = new URL(API_BASE_URL);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  u.pathname = path;
  return u.toString();
}
