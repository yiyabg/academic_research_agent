/**
 * Server-side API client for calling the FastAPI backend.
 * This module is used by Next.js API routes to proxy requests.
 * IMPORTANT: This file should only be imported in server-side code (API routes, Server Components).
 */

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";
const DEFAULT_BACKEND_TIMEOUT_MS = 30_000;

export class BackendApiTimeoutError extends Error {
  constructor(public readonly timeoutMs: number) {
    super(`Backend API request timed out after ${timeoutMs}ms`);
    this.name = "BackendApiTimeoutError";
  }
}

export class BackendApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public data?: unknown,
  ) {
    super(`Backend API error: ${status} ${statusText}`);
    this.name = "BackendApiError";
  }
}

interface RequestOptions extends RequestInit {
  params?: Record<string, string>;
  /** Return raw text instead of parsing as JSON */
  raw?: boolean;
  /** Abort the internal BFF -> FastAPI request after this many milliseconds. */
  timeoutMs?: number;
}

/**
 * Make a request to the FastAPI backend.
 * This should only be called from Next.js API routes or Server Components.
 */
export async function backendFetch<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const { params, body, raw, timeoutMs = DEFAULT_BACKEND_TIMEOUT_MS, ...fetchOptions } = options;

  let url = `${BACKEND_URL}${endpoint}`;

  if (params) {
    const searchParams = new URLSearchParams(params);
    url += `?${searchParams.toString()}`;
  }

  // Determine content type - don't set for FormData (browser will set with boundary)
  const headers: Record<string, string> = {};
  if (body instanceof FormData) {
    // Let the browser set Content-Type with the multipart boundary
  } else {
    headers["Content-Type"] = "application/json";
  }

  // A Next.js route handler otherwise waits indefinitely when the FastAPI
  // process is reloading or unavailable. Preserve a caller-provided AbortSignal
  // while adding a server-side deadline of our own.
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(fetchOptions.signal?.reason);
  if (fetchOptions.signal?.aborted) {
    abortFromCaller();
  } else {
    fetchOptions.signal?.addEventListener("abort", abortFromCaller, { once: true });
  }
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  let response: Response;
  try {
    response = await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        ...headers,
        ...fetchOptions.headers,
      },
      body,
    });
  } catch (error) {
    if (timedOut) throw new BackendApiTimeoutError(timeoutMs);
    throw error;
  } finally {
    clearTimeout(timeout);
    fetchOptions.signal?.removeEventListener("abort", abortFromCaller);
  }

  if (!response.ok) {
    let errorData;
    try {
      errorData = await response.json();
    } catch {
      errorData = null;
    }
    throw new BackendApiError(response.status, response.statusText, errorData);
  }

  // Handle empty responses
  const text = await response.text();
  if (!text) {
    return null as T;
  }

  if (raw) {
    return text as T;
  }

  return JSON.parse(text);
}

/**
 * Forward authorization header from the incoming request to the backend.
 */
export function getAuthHeaders(authHeader: string | null): Record<string, string> {
  if (!authHeader) {
    return {};
  }
  return { Authorization: authHeader };
}

/**
 * Preserve the browser address through the internal Next.js → FastAPI hop.
 * The backend accepts this header only when the direct peer belongs to its
 * configured trusted-proxy CIDRs.
 */
export function getClientIpHeaders(headers: Headers): Record<string, string> {
  const forwarded = headers.get("x-forwarded-for")?.split(",", 1)[0]?.trim();
  if (forwarded) return { "X-Forwarded-For": forwarded };
  const realIp = headers.get("x-real-ip")?.trim();
  return realIp ? { "X-Forwarded-For": realIp } : {};
}
