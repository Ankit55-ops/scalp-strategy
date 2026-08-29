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

const TOKEN_KEY = "fxscalper_token";
const EMAIL_KEY = "fxscalper_email";

export const tokenStore = {
  get: (): string | null => {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(TOKEN_KEY);
  },
  set: (token: string) => {
    window.localStorage.setItem(TOKEN_KEY, token);
  },
  clear: () => {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(EMAIL_KEY);
  },
  getEmail: (): string | null => {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(EMAIL_KEY);
  },
  setEmail: (email: string) => window.localStorage.setItem(EMAIL_KEY, email),
};