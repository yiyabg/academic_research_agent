import { Check, Circle, LoaderCircle, X } from "lucide-react";

import { cn } from "@/lib/utils";
import type { RunState } from "@/types/literature-research";

export const PIPELINE_STAGES: RunState[] = [
  "QUEUED",
  "DISCOVERING",
  "NORMALIZING",
  "ENRICHING_METRICS",
  "DEDUPLICATING",
  "HARD_FILTERING",
  "RELEVANCE_SCORING",
  "FULLTEXT_ACQUIRING",
  "PARSING",
  "SELECTING",
  "ANALYZING",
  "EVIDENCE_AUDITING",
  "SYNTHESIZING",
  "RENDERING",
  "RELEASE_CHECKING",
  "COMPLETED",
];

const labels: Partial<Record<RunState, string>> = {
  QUEUED: "排队",
  DISCOVERING: "多源发现",
  NORMALIZING: "元数据归一化",
  ENRICHING_METRICS: "质量指标",
  DEDUPLICATING: "版本聚合与去重",
  HARD_FILTERING: "硬约束筛选",
  RELEVANCE_SCORING: "相关性判定",
  FULLTEXT_ACQUIRING: "合法全文获取",
  PARSING: "正文与图表解析",
  SELECTING: "严格选择",
  ANALYZING: "并行深度分析",
  EVIDENCE_AUDITING: "证据审计",
  SYNTHESIZING: "跨论文综合",
  RENDERING: "确定性导出",
  RELEASE_CHECKING: "发布门禁",
  COMPLETED: "完成",
};

export function stagePosition(state: RunState): number {
  const index = PIPELINE_STAGES.indexOf(state);
  if (state === "PARTIALLY_COMPLETED") return PIPELINE_STAGES.length - 1;
  return Math.max(index, 0);
}

export function ResearchStageTimeline({ state }: { state: RunState }) {
  const current = stagePosition(state);
  const failed = state.startsWith("FAILED") || state === "CANCELLED";
  return (
    <ol className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4" aria-label="研究运行状态时间线">
      {PIPELINE_STAGES.slice(0, -1).map((stage, index) => {
        const complete = index < current;
        const active = index === current && !failed;
        const Icon = complete ? Check : active ? LoaderCircle : failed && index === current ? X : Circle;
        return (
          <li
            key={stage}
            className={cn(
              "flex items-center gap-2 rounded-lg border px-3 py-2 text-xs",
              complete && "border-emerald-500/25 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300",
              active && "border-brand/40 bg-brand/5 text-foreground",
              !complete && !active && "text-muted-foreground",
            )}
          >
            <Icon className={cn("h-4 w-4 shrink-0", active && "animate-spin")} />
            <span>{labels[stage] ?? stage}</span>
          </li>
        );
      })}
    </ol>
  );
}
