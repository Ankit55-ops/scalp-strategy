const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

type ApiOptions = {
  method?: string;
  body?: unknown;
  token?: string | null;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(
  path: string,
  { method = "GET", body, token }: ApiOptions = {}
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!res.ok) {
    let message = `request failed (${res.status})`;
    try {
      const data = await res.json();
      if (data?.detail) message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* ignore */
    }
    if (res.status === 401) {
      if (typeof window !== "undefined") window.location.href = "/login";
    }
    throw new ApiError(res.status, message);
  }
  const text = await res.text();
  return text ? (JSON.parse(text) as T) : ({} as T);
}

// Token storage: sessionStorage only (tab-scoped, cleared when the tab closes),
// backed by a module-level variable as a fallback so the token survives even if
// storage is unavailable. NEVER persisted to localStorage - XSS-safe by design.
const TOKEN_KEY = "fxscalper_token";
const EMAIL_KEY = "fxscalper_email";

let memoryToken: string | null = null;
let memoryEmail: string | null = null;

function storageGet(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    /* private-mode storage may be unavailable; in-memory copy still holds it */
  }
}

function storageRemove(key: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

export const tokenStore = {
  get: (): string | null => {
    if (memoryToken !== null) return memoryToken;
    const stored = storageGet(TOKEN_KEY);
    if (stored) memoryToken = stored;
    return stored;
  },
  set: (token: string) => {
    memoryToken = token;
    storageSet(TOKEN_KEY, token);
  },
  clear: () => {
    memoryToken = null;
    memoryEmail = null;
    storageRemove(TOKEN_KEY);
    storageRemove(EMAIL_KEY);
  },
  getEmail: (): string | null => {
    if (memoryEmail !== null) return memoryEmail;
    const stored = storageGet(EMAIL_KEY);
    if (stored) memoryEmail = stored;
    return stored;
  },
  setEmail: (email: string) => {
    memoryEmail = email;
    storageSet(EMAIL_KEY, email);
  },
};