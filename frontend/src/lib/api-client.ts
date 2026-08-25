/**
 * Client-side API client.
 * All requests go through Next.js API routes (/api/*), never directly to the backend.
 * This keeps the backend URL hidden from the browser.
 */

import { useAuthStore } from "@/stores";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * API errors are not consistently shaped: FastAPI validation returns arrays,
 * while the rate limiter nests its text under `detail.error.message`.
 * React must only ever receive a string as an error message.
 */
export function apiErrorMessage(value: unknown, fallback = "Request failed"): string {
  if (typeof value === "string" && value.trim()) return value;
  if (Array.isArray(value)) {
    const first = value.find((item) => apiErrorMessage(item, "") !== "");
    return first === undefined ? fallback : apiErrorMessage(first, fallback);
  }
  if (isRecord(value)) {
    for (const key of ["message", "detail", "error"]) {
      const message = apiErrorMessage(value[key], "");
      if (message) return message;
    }
  }
  return fallback;
}

export class ApiError extends Error {
  public readonly status: number;
  public readonly data?: unknown;

  constructor(status: number, message: unknown, data?: unknown) {
    super(apiErrorMessage(message));
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  params?: Record<string, string>;
  body?: unknown;
  /** Browser-side deadline. Prevents a disabled form from waiting forever. */
  timeoutMs?: number;
}

const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(init.signal?.reason);
  if (init.signal?.aborted) {
    abortFromCaller();
  } else {
    init.signal?.addEventListener("abort", abortFromCaller, { once: true });
  }
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (error) {
    if (timedOut) {
      throw new ApiError(408, "请求超时，服务暂时不可用，请稍后重试。");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
    init.signal?.removeEventListener("abort", abortFromCaller);
  }
}

// The proxy route that mints a fresh access token from the refresh cookie.
const REFRESH_ENDPOINT = "/auth/refresh";

// Shared in-flight refresh promise so a burst of concurrent 401s triggers only
// ONE refresh round-trip. Reset once the refresh settles.
let refreshPromise: Promise<boolean> | null = null;

/**
 * Attempt a single token refresh, de-duplicating concurrent callers.
 * Resolves true on success (cookies + in-memory access token updated), false
 * if the refresh itself failed (caller should surface the original 401).
 */
function refreshAccessToken(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = fetchWithTimeout(
      `/api${REFRESH_ENDPOINT}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      },
      DEFAULT_REQUEST_TIMEOUT_MS,
    )
      .then(async (res) => {
        if (!res.ok) return false;
        try {
          const data = (await res.json()) as { access_token?: string };
          if (data?.access_token) {
            // Keep the in-memory token (used for WS auth) in sync.
            useAuthStore.getState().setAccessToken(data.access_token);
          }
        } catch {
          // Body wasn't JSON — cookies were still rotated, treat as success.
        }
        return true;
      })
      .catch(() => false)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

class ApiClient {
  private async request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
    const { params, body, timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS, ...fetchOptions } = options;

    let url = `/api${endpoint}`;

    if (params) {
      const searchParams = new URLSearchParams(params);
      url += `?${searchParams.toString()}`;
    }

    const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
    const doFetch = () => {
      const headers: HeadersInit = {
        ...(!isFormData && body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...fetchOptions.headers,
      };
      return fetchWithTimeout(
        url,
        {
          ...fetchOptions,
          headers,
          body:
            body === undefined ? undefined : isFormData ? (body as FormData) : JSON.stringify(body),
        },
        timeoutMs,
      );
    };

    let response = await doFetch();

    // Transparent 401 recovery: refresh once, then retry the request once.
    // Never recurse into the refresh endpoint itself (would loop), and only
    // attempt this a single time per call.
    if (
      response.status === 401 &&
      endpoint !== REFRESH_ENDPOINT &&
      endpoint !== "/auth/login" &&
      endpoint !== "/auth/register"
    ) {
      const refreshed = await refreshAccessToken();
      if (refreshed) {
        response = await doFetch();
      }
    }

    if (!response.ok) {
      let errorData;
      try {
        errorData = await response.json();
      } catch {
        errorData = null;
      }
      throw new ApiError(response.status, apiErrorMessage(errorData), errorData);
    }

    // Handle empty responses
    const text = await response.text();
    if (!text) {
      return null as T;
    }

    return JSON.parse(text);
  }

  get<T>(endpoint: string, options?: RequestOptions) {
    return this.request<T>(endpoint, { ...options, method: "GET" });
  }

  post<T>(endpoint: string, body?: unknown, options?: RequestOptions) {
    return this.request<T>(endpoint, { ...options, method: "POST", body });
  }

  put<T>(endpoint: string, body?: unknown, options?: RequestOptions) {
    return this.request<T>(endpoint, { ...options, method: "PUT", body });
  }

  patch<T>(endpoint: string, body?: unknown, options?: RequestOptions) {
    return this.request<T>(endpoint, { ...options, method: "PATCH", body });
  }

  delete<T>(endpoint: string, options?: RequestOptions) {
    return this.request<T>(endpoint, { ...options, method: "DELETE" });
  }
}

export const apiClient = new ApiClient();
