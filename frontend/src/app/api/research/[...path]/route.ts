import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

interface RouteParams {
  params: Promise<{ path: string[] }>;
}

async function proxy(request: NextRequest, { params }: RouteParams) {
  const accessToken = request.cookies.get("access_token")?.value;
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }
  const { path } = await params;
  const encodedPath = path.map(encodeURIComponent).join("/");
  const query = request.nextUrl.searchParams.toString();
  const url = `${BACKEND_URL}/api/v1/research/${encodedPath}${query ? `?${query}` : ""}`;
  const headers: Record<string, string> = { Authorization: `Bearer ${accessToken}` };
  const contentType = request.headers.get("content-type");
  if (contentType) headers["Content-Type"] = contentType;
  const researchOrganization = request.headers.get("x-research-organization-id");
  if (researchOrganization) {
    headers["X-Research-Organization-ID"] = researchOrganization;
  }

  try {
    const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer();
    const upstream = await fetch(url, { method: request.method, headers, body });
    const payload = await upstream.arrayBuffer();
    const responseHeaders = new Headers();
    for (const name of ["content-type", "content-disposition", "x-content-sha256"]) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new NextResponse(payload, { status: upstream.status, headers: responseHeaders });
  } catch {
    return NextResponse.json({ detail: "Research backend unavailable" }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
