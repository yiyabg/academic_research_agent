import { NextResponse } from "next/server";

import { backendFetch, BackendApiError } from "@/lib/server-api";
import type { ResearchReadiness } from "@/types/literature-research";

export async function GET() {
  try {
    const data = await backendFetch<ResearchReadiness>("/api/v1/health/ready");
    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: "Research dependencies are not ready" },
        { status: error.status },
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
