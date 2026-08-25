import { AlertTriangle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle, Button } from "@/components/ui";
import type { ResearchRun } from "@/types/literature-research";

export function ShortfallCard({
  run,
  busy,
  onAccept,
  onCancel,
}: {
  run: ResearchRun;
  busy: boolean;
  onAccept: () => void;
  onCancel: () => void;
}) {
  if (run.state !== "AWAITING_RELAXATION_AUTHORIZATION") return null;
  return (
    <Alert variant="warning">
      <AlertTriangle />
      <AlertTitle>严格结果数量不足，但质量门槛未降低</AlertTitle>
      <AlertDescription>
        <p>
          目标 {run.target_count} 篇，严格合格 {run.strict_count} 篇。你可以接受严格短缺继续分析，或取消运行；如需放宽条件，必须创建并重新批准协议版本。
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" disabled={busy} onClick={onAccept}>接受 {run.strict_count} 篇并继续</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={onCancel}>取消运行</Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}
