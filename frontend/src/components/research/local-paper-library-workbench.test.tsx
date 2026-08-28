import { describe, expect, it } from "vitest";

import {
  analysisStageLabel,
  buildLocalPaperSearchRequest,
  queryInterpretationChips,
} from "./local-paper-library-workbench";
import type { LocalPaperAnalysisJob, QueryInterpretation } from "@/types/literature-research";

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

describe("local-paper search controls", () => {
  it("sends explicit advanced filters and normalized keyword lists", () => {
    expect(
      buildLocalPaperSearchRequest("semantic communication", 10, {
        year_from: "2024",
        year_to: "2026",
        author: "张三",
        doi: "10.1000/example",
        venue: "IEEE TWC",
        bibtex_type: "article",
        keywords: "semantic communication, VLA，",
      }),
    ).toEqual({
      query: "semantic communication",
      limit: 10,
      year_from: 2024,
      year_to: 2026,
      author: "张三",
      doi: "10.1000/example",
      venue: "IEEE TWC",
      bibtex_type: "article",
      keywords: ["semantic communication", "VLA"],
    });
  });

  it("renders semantic, filter-source, and warning chips from the actual interpretation", () => {
    const interpretation: QueryInterpretation = {
      raw_query: "2026年发表的 semantic communication",
      semantic_query: "semantic communication",
      effective_filters: { year_from: 2026, year_to: 2026, venue: "IEEE TWC" },
      filter_sources: { year_from: "parsed", year_to: "parsed", venue: "explicit" },
      warnings: ["年份范围无效已忽略"],
    };

    expect(queryInterpretationChips(interpretation)).toEqual([
      { key: "semantic", label: "语义：semantic communication", kind: "semantic" },
      { key: "filter-year_from", label: "year_from: 2026（parsed）", kind: "filter" },
      { key: "filter-year_to", label: "year_to: 2026（parsed）", kind: "filter" },
      { key: "filter-venue", label: "venue: IEEE TWC（explicit）", kind: "filter" },
      { key: "warning-0", label: "年份范围无效已忽略", kind: "warning" },
    ]);
  });
});
