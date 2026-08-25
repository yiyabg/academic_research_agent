import { NextRequest, NextResponse } from "next/server";
import { setAuthCookies } from "@/lib/auth-cookies";
import {
  backendFetch,
  BackendApiError,
  BackendApiTimeoutError,
  getClientIpHeaders,
} from "@/lib/server-api";
import type { LoginResponse } from "@/types";

const AUTH_BACKEND_TIMEOUT_MS = 8_000;

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    // Backend expects OAuth2 form data format
    const formData = new URLSearchParams();
    formData.append("username", body.email);
    formData.append("password", body.password);

    const data = await backendFetch<LoginResponse>("/api/v1/auth/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        ...getClientIpHeaders(request.headers),
      },
      body: formData.toString(),
      timeoutMs: AUTH_BACKEND_TIMEOUT_MS,
    });

    const user = await backendFetch("/api/v1/auth/me", {
      headers: { Authorization: `Bearer ${data.access_token}` },
      timeoutMs: AUTH_BACKEND_TIMEOUT_MS,
    });

    // Set HTTP-only cookies for tokens. Also return the access_token in the
    // body so the client can use it for cross-origin WebSocket auth.
    const response = NextResponse.json({
      user,
      access_token: data.access_token,
      message: "Login successful",
    });

    setAuthCookies(response, {
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
    });

    return response;
  } catch (error) {
    if (error instanceof BackendApiTimeoutError) {
      return NextResponse.json({ detail: "认证服务响应超时，请稍后重试。" }, { status: 503 });
    }
    if (error instanceof BackendApiError) {
      const detail = (error.data as { detail?: string })?.detail || "Login failed";
      return NextResponse.json({ detail }, { status: error.status });
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
