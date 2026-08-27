import { describe, expect, it } from "vitest";

import { analysisStageLabel } from "./local-paper-library-workbench";
import type { LocalPaperAnalysisJob } from "@/types/literature-research";

const job = (overrides: Partial<LocalPaperAnalysisJob>): LocalPaperAnalysisJob =>
  ({
    id: "00000000-0000-4000-8000-000000000001",
    session_id: "00000000-0000-4000-8000-000000000002",
    library_id: "00000000-0000-4000-8000-000000000003",
    owner_id: "00000000-0000-4000-8000-000000000004",
    status: "ANALYZING",
    stage: "EVIDENCE_READY",
    stage_index: 0,
    stage_total: 6,
    execution_mode: "staged",
    provider_status: null,
    question: "test",
    mode: "FOCUSED",
    retrieval_run_id: null,
    result: {},
    error_code: null,
    error_message: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }) as LocalPaperAnalysisJob;

describe("analysisStageLabel", () => {
  it("shows bounded per-paper progress while an analysis is active", () => {
    expect(analysisStageLabel(job({ stage: "PAPER_2_COMPLETED", stage_index: 2 }), "处理中")).toBe(
      "正在分析第 3/5 篇论文",
    );
  });

  it("uses safe terminal and background states", () => {
    expect(analysisStageLabel(job({ status: "PARTIAL" }), "处理中")).toBe("部分完成");
    expect(
      analysisStageLabel(
        job({ execution_mode: "background", provider_status: "in_progress" }),
        "处理中",
      ),
    ).toBe("模型正在分析");
  });
});
