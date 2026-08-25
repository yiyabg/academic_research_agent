import { Progress } from "@/components/ui";
import type { ResearchRun } from "@/types/literature-research";

export interface FunnelStep {
  label: string;
  value: number;
}

export function buildResearchFunnel(run: ResearchRun): FunnelStep[] {
  const loss =
    run.progress.loss_funnel && typeof run.progress.loss_funnel === "object"
      ? (run.progress.loss_funnel as Record<string, number>)
      : {};
  return [
    { label: "Raw records", value: Number(loss.raw ?? run.candidate_count) },
    { label: "Canonical", value: Number(loss.canonical ?? run.candidate_count) },
    { label: "Date pass", value: Number(loss.date_pass ?? run.strict_count) },
    { label: "Metric pass", value: Number(loss.metric_pass ?? run.strict_count) },
    { label: "Relevance pass", value: Number(loss.relevance_pass ?? run.strict_count) },
    { label: "Evidence pass", value: Number(loss.evidence_pass ?? run.analyzed_count) },
  ];
}

export function ResearchFunnel({ run }: { run: ResearchRun }) {
  const steps = buildResearchFunnel(run);
  const maximum = Math.max(...steps.map((item) => item.value), 1);
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {steps.map((step) => (
        <div key={step.label} className="rounded-lg border p-3">
          <div className="mb-2 flex items-center justify-between gap-3 text-xs">
            <span className="text-muted-foreground font-mono uppercase">{step.label}</span>
            <span className="text-foreground font-semibold tabular-nums">{step.value}</span>
          </div>
          <Progress value={(step.value / maximum) * 100} />
        </div>
      ))}
    </div>
  );
}
