// Auth token storage: an in-memory variable is authoritative for the tab,
// mirrored to localStorage so a refresh survives. The backend is stateless
// HS256 with no refresh endpoint, so expiry => re-login (8-day window).
const STORAGE_KEY = "wso.token";

function readMirror(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

let inMemoryToken: string | null = readMirror();

export function getToken(): string | null {
  return inMemoryToken;
}

export function setToken(token: string): void {
  inMemoryToken = token;
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, token);
  } catch {
    // storage unavailable (private mode / quota) — in-memory still authoritative
  }
}

export function clearToken(): void {
  inMemoryToken = null;
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage unavailable — in-memory already cleared
  }
}
