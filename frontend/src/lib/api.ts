/**
 * Typed API client.
 * - Stores the access token in memory + sessionStorage (refresh token is an httpOnly cookie).
 * - Sends the double-submit CSRF header on cookie-based calls.
 * - Normalises the backend error envelope into ApiError.
 * - Transparently refreshes once on 401 and retries the original request.
 */

export type ApiErrorPayload = {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  code?: string;
  errors?: { field?: string; message?: string }[];
  request_id?: string;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorPayload;
  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.detail ?? payload.title ?? `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.code = payload.code ?? "error";
    this.details = payload;
  }
}

const TOKEN_KEY = "procureai.access";
const CSRF_COOKIE = "procureai_csrf";

type TokenListener = (token: string | null) => void;
const listeners = new Set<TokenListener>();

export const session = {
  get: (): string | null => sessionStorage.getItem(TOKEN_KEY),
  set(token: string | null) {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
    listeners.forEach((fn) => fn(token));
  },
  subscribe(fn: TokenListener) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
};

function readCookie(name: string): string {
  const match = document.cookie.split("; ").find((row) => row.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.split("=").slice(1).join("=")) : "";
}

export type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  raw?: boolean;
  skipAuth?: boolean;
  retryOn401?: boolean;
};

const BASE = import.meta.env.VITE_API_BASE ?? "";

let refreshInFlight: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  refreshInFlight ??= (async () => {
    try {
      const resp = await fetch(`${BASE}/api/v1/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "x-csrf-token": readCookie(CSRF_COOKIE) },
      });
      if (!resp.ok) return false;
      const data = (await resp.json()) as { access_token?: string };
      if (data.access_token) {
        session.set(data.access_token);
        return true;
      }
      return false;
    } catch {
      return false;
    } finally {
      // Allow a later refresh attempt once this one settles.
      setTimeout(() => {
        refreshInFlight = null;
      }, 0);
    }
  })();
  return refreshInFlight;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, raw = false, skipAuth = false, retryOn401 = true } = options;
  const headers: Record<string, string> = { accept: "application/json" };
  const token = session.get();
  if (token && !skipAuth) headers.authorization = `Bearer ${token}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  if (!skipAuth) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers["x-csrf-token"] = csrf;
  }

  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers,
    credentials: "include",
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (resp.status === 401 && retryOn401 && !skipAuth && (await refreshSession())) {
    return request<T>(path, { ...options, retryOn401: false });
  }

  if (!resp.ok) {
    let payload: ApiErrorPayload = { status: resp.status, title: resp.statusText };
    try {
      payload = (await resp.json()) as ApiErrorPayload;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, payload);
  }

  if (resp.status === 204) return undefined as T;
  if (raw) return (await resp.blob()) as T;
  const text = await resp.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "PATCH", body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: "PUT", body }),
  delete: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: "DELETE" }),
  download: async (path: string, filename: string) => {
    const blob = await request<Blob>(path, { raw: true });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  },
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.details.errors?.length) {
      return error.details.errors.map((e) => e.message ?? e.field ?? "").filter(Boolean).join(" · ");
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}
