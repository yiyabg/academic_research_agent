import { Badge, Progress } from "@/components/ui";
import type { EvaluationReport } from "@/types/literature-research";

export function EvaluationDashboard({ reports }: { reports: EvaluationReport[] }) {
  const latest = reports[0];
  if (!latest) return <p className="text-muted-foreground text-sm">尚未针对人工金标准集运行评测。</p>;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Badge variant={latest.passed ? "default" : "destructive"}>{latest.passed ? "EVAL PASS" : "EVAL INCOMPLETE/FAIL"}</Badge>
        <span className="text-muted-foreground text-xs">{new Date(latest.evaluated_at).toLocaleString()}</span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Object.entries(latest.metrics).map(([name, metric]) => (
          <div key={name} className="rounded-lg border p-3">
            <div className="mb-2 flex justify-between gap-2 text-xs">
              <span className="font-mono">{name}</span><span>{metric.status}</span>
            </div>
            <Progress value={(metric.value ?? 0) * 100} />
            <p className="text-muted-foreground mt-2 text-[11px] tabular-nums">
              {metric.value === undefined ? "not evaluated" : metric.value.toFixed(3)} / threshold {metric.threshold.toFixed(2)} · n={metric.sample_size}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
