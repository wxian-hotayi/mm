/**
 * Typed fetch wrapper for the WealthOS backend (DESIGN §20.8, §5).
 *
 * - Base URL is `/api/v1`; in dev Vite proxies this to FastAPI on :8000.
 * - Cookie auth: every request sends `credentials: "include"` so the HttpOnly
 *   `wos_access` / `wos_refresh` session cookies travel automatically.
 * - On a `401`, the client POSTs `/auth/refresh` exactly ONCE (a shared
 *   in-flight promise prevents loops / stampedes) and retries the original
 *   request. If refresh fails, it clears auth state and redirects to `/login`.
 * - JSON in / JSON out; errors surface as a typed `ApiError`.
 *
 * Components never call this directly — they go through `api/endpoints.ts`.
 */

const API_BASE = "/api/v1";
const REFRESH_PATH = "/auth/refresh";
const LOGIN_ROUTE = "/login";

/** Structured API error thrown by the client (DESIGN §8 error body). */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly code?: string;

  constructor(status: number, detail: string, code?: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

/** Options accepted by the request helper. */
export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  /** JSON-serializable request body. */
  body?: unknown;
  /** Query parameters; `undefined` / `null` values are dropped. */
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  /** Skip the refresh-on-401 retry (used by the refresh call itself). */
  skipAuthRetry?: boolean;
}

/**
 * Listeners notified when the session is irrecoverably lost (refresh failed).
 * `useAuth` registers here to clear the cached user before the redirect.
 */
type AuthFailureListener = () => void;
const authFailureListeners = new Set<AuthFailureListener>();

/** Register a callback invoked once when the session can no longer be refreshed. */
export function onAuthFailure(listener: AuthFailureListener): () => void {
  authFailureListeners.add(listener);
  return () => {
    authFailureListeners.delete(listener);
  };
}

function notifyAuthFailure(): void {
  for (const listener of authFailureListeners) {
    listener();
  }
}

/** Shared in-flight refresh promise so concurrent 401s trigger one refresh. */
let refreshInFlight: Promise<boolean> | null = null;

function buildUrl(
  path: string,
  query?: RequestOptions["query"],
): string {
  const url = `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    params.append(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function parseError(response: Response): Promise<ApiError> {
  let detail = response.statusText || "Request failed";
  let code: string | undefined;
  try {
    // FastAPI returns `{detail: string, code?}` for AppErrors and a `detail`
    // array of `{msg}` objects for 422 validation. Type both shapes directly
    // (intersecting with ApiErrorBody would collapse the array to `never`).
    const data = (await response.json()) as {
      detail?: string | Array<{ msg?: string }>;
      code?: string;
    };
    if (typeof data.detail === "string") {
      detail = data.detail;
    } else if (Array.isArray(data.detail)) {
      const messages = data.detail
        .map((item) => (typeof item?.msg === "string" ? item.msg : null))
        .filter((msg): msg is string => msg !== null);
      if (messages.length > 0) detail = messages.join("; ");
    }
    if (typeof data.code === "string") code = data.code;
  } catch {
    // Non-JSON error body — keep the status text.
  }
  return new ApiError(response.status, detail, code);
}

/** POST `/auth/refresh` once; returns whether a fresh session was obtained. */
async function attemptRefresh(): Promise<boolean> {
  if (refreshInFlight === null) {
    refreshInFlight = (async (): Promise<boolean> => {
      try {
        const response = await fetch(`${API_BASE}${REFRESH_PATH}`, {
          method: "POST",
          credentials: "include",
          headers: { Accept: "application/json" },
        });
        return response.ok;
      } catch {
        return false;
      } finally {
        // Allow a subsequent refresh after this one settles.
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}

function redirectToLogin(): void {
  notifyAuthFailure();
  if (
    typeof window !== "undefined" &&
    window.location.pathname !== LOGIN_ROUTE
  ) {
    window.location.assign(LOGIN_ROUTE);
  }
}

async function rawFetch(
  path: string,
  options: RequestOptions,
): Promise<Response> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let serializedBody: string | undefined;
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    serializedBody = JSON.stringify(options.body);
  }
  return fetch(buildUrl(path, options.query), {
    method: options.method ?? "GET",
    credentials: "include",
    headers,
    body: serializedBody,
    signal: options.signal,
  });
}

/**
 * Perform a typed request. On a 401 (outside the auth endpoints) it refreshes
 * the session once and retries; a second 401 clears auth and redirects.
 */
export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  let response = await rawFetch(path, options);

  if (
    response.status === 401 &&
    !options.skipAuthRetry &&
    path !== REFRESH_PATH
  ) {
    const refreshed = await attemptRefresh();
    if (refreshed) {
      response = await rawFetch(path, options);
    }
    if (!refreshed || response.status === 401) {
      redirectToLogin();
      throw await parseError(response);
    }
  }

  if (!response.ok) {
    throw await parseError(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

/** Convenience verbs. */
export const apiClient = {
  get: <T>(path: string, query?: RequestOptions["query"], signal?: AbortSignal) =>
    request<T>(path, { method: "GET", query, signal }),
  post: <T>(path: string, body?: unknown, query?: RequestOptions["query"]) =>
    request<T>(path, { method: "POST", body, query }),
  patch: <T>(path: string, body?: unknown, query?: RequestOptions["query"]) =>
    request<T>(path, { method: "PATCH", body, query }),
  put: <T>(path: string, body?: unknown, query?: RequestOptions["query"]) =>
    request<T>(path, { method: "PUT", body, query }),
  delete: <T>(path: string, query?: RequestOptions["query"]) =>
    request<T>(path, { method: "DELETE", query }),
} as const;
