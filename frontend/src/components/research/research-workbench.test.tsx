import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { buildResearchFunnel } from "./research-funnel";
import { ResearchStageTimeline, stagePosition } from "./research-stage-timeline";
import { ShortfallCard } from "./shortfall-card";
import type { ResearchRun } from "@/types/literature-research";

function run(overrides: Partial<ResearchRun> = {}): ResearchRun {
  return {
    id: "run-1",
    project_id: "project-1",
    owner_id: "user-1",
    organization_id: null,
    protocol_version_id: "protocol-1",
    state: "SELECTING",
    state_version: 8,
    execution_mode: "full_research",
    protocol_hash: `sha256:${"a".repeat(64)}`,
    target_count: 20,
    strict_count: 7,
    candidate_count: 120,
    analyzed_count: 0,
    progress: { loss_funnel: { raw: 300, canonical: 120, date_pass: 70, metric_pass: 31 } },
    created_at: "2026-08-21T00:00:00Z",
    ...overrides,
  };
}

describe("research workbench", () => {
  it("renders named stages instead of an opaque percentage", () => {
    render(<ResearchStageTimeline state="HARD_FILTERING" />);
    expect(screen.getByText("多源发现")).toBeInTheDocument();
    expect(screen.getByText("硬约束筛选")).toBeInTheDocument();
    expect(stagePosition("PARTIALLY_COMPLETED")).toBeGreaterThan(stagePosition("ANALYZING"));
  });

  it("builds the explainable raw-to-evidence funnel", () => {
    expect(buildResearchFunnel(run()).map((item) => item.value)).toEqual([300, 120, 70, 31, 7, 0]);
  });

  it("requires an explicit user action for strict shortfall", () => {
    const accept = vi.fn();
    const cancel = vi.fn();
    render(
      <ShortfallCard
        run={run({ state: "AWAITING_RELAXATION_AUTHORIZATION" })}
        busy={false}
        onAccept={accept}
        onCancel={cancel}
      />,
    );
    expect(screen.getByText(/质量门槛未降低/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /接受 7 篇/ }));
    expect(accept).toHaveBeenCalledOnce();
    expect(cancel).not.toHaveBeenCalled();
  });
});
