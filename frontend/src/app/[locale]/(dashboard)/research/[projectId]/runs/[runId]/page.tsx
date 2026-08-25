"use client";

import { use, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PauseCircle, PlayCircle, RefreshCcw, XCircle } from "lucide-react";

import { PageHeader } from "@/components/dashboard/page-header";
import {
  ArtifactDownloads,
  CandidateTable,
  EvaluationDashboard,
  EvaluationControls,
  PaperDetailPanel,
  ResearchFunnel,
  ResearchStageTimeline,
  RunEventSync,
  ShortfallCard,
} from "@/components/research";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/components/ui";
import { literatureResearchApi } from "@/lib/literature-research-api";
import { qk } from "@/lib/query-keys";
import { useLiteratureResearchStore } from "@/stores";
import type { ResearchRelevanceFeedbackDecision } from "@/types/literature-research";

interface PageParams {
  params: Promise<{ projectId: string; runId: string }>;
}

interface LlmUsageTotal {
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: string | null;
  cost_status: "REPORTED" | "UNAVAILABLE";
}

function llmUsageTotal(progress: Record<string, unknown>, key: string): LlmUsageTotal | null {
  const snapshot = progress[key];
  if (!snapshot || typeof snapshot !== "object" || !("total" in snapshot)) return null;
  const total = snapshot.total;
  if (!total || typeof total !== "object" || !("total_tokens" in total)) return null;
  return total as LlmUsageTotal;
}

export default function ResearchRunPage({ params }: PageParams) {
  const { projectId, runId } = use(params);
  const queryClient = useQueryClient();
  const selectedWorkId = useLiteratureResearchStore((state) => state.selectedWorkId);
  const selectWork = useLiteratureResearchStore((state) => state.selectWork);
  const [actionError, setActionError] = useState<string | null>(null);
  const [feedbackStatus, setFeedbackStatus] = useState<string | null>(null);
  const run = useQuery({
    queryKey: qk.literatureResearch.run(runId),
    queryFn: () => literatureResearchApi.getRun(runId),
    refetchInterval: 5_000,
  });
  const candidates = useQuery({
    queryKey: qk.literatureResearch.candidates(runId),
    queryFn: () => literatureResearchApi.candidates(runId),
    refetchInterval: 10_000,
  });
  const paper = useQuery({
    queryKey: qk.literatureResearch.paper(runId, selectedWorkId),
    queryFn: () => literatureResearchApi.paper(runId, selectedWorkId!),
    enabled: Boolean(selectedWorkId),
  });
  const artifacts = useQuery({
    queryKey: qk.literatureResearch.artifacts(runId),
    queryFn: () => literatureResearchApi.artifacts(runId),
    refetchInterval: 15_000,
  });
  const evaluations = useQuery({
    queryKey: qk.literatureResearch.evaluations(runId),
    queryFn: () => literatureResearchApi.evaluations(runId),
    refetchInterval: 30_000,
  });
  const action = useMutation({
    mutationFn: (name: "accept" | "cancel" | "pause" | "resume") =>
      name === "accept"
        ? literatureResearchApi.shortfallAction(runId, "accept_strict_shortfall")
        : name === "cancel"
          ? literatureResearchApi.cancel(runId)
          : name === "pause"
            ? literatureResearchApi.pause(runId)
            : literatureResearchApi.resume(runId),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: qk.literatureResearch.run(runId) }),
    onError: (reason) => setActionError(reason instanceof Error ? reason.message : "操作失败"),
  });
  const reanalyze = useMutation({
    mutationFn: () => literatureResearchApi.reanalyze(runId, selectedWorkId!),
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: qk.literatureResearch.paper(runId, selectedWorkId),
      }),
    onError: (reason) => setActionError(reason instanceof Error ? reason.message : "重分析失败"),
  });
  const relevanceFeedback = useMutation({
    mutationFn: (decision: ResearchRelevanceFeedbackDecision) => {
      if (!selectedWorkId) throw new Error("缺少论文");
      setFeedbackStatus(null);
      return literatureResearchApi.submitRelevanceFeedback(runId, selectedWorkId, decision);
    },
    onSuccess: (accepted, decision) => {
      setActionError(null);
      setFeedbackStatus(
        `${decision === "EXCLUDE" ? "排除" : "核心相关"}反馈已保存 · memory ${
          accepted.project_memory_id?.slice(0, 8) ?? "pending"
        }`,
      );
    },
    onError: (reason) => setActionError(reason instanceof Error ? reason.message : "反馈保存失败"),
  });
  const regenerate = useMutation({
    mutationFn: () => literatureResearchApi.regenerateArtifacts(runId),
    onSuccess: () => {
      setActionError(null);
      void queryClient.invalidateQueries({ queryKey: qk.literatureResearch.run(runId) });
      void queryClient.invalidateQueries({ queryKey: qk.literatureResearch.artifacts(runId) });
    },
    onError: (reason) =>
      setActionError(reason instanceof Error ? reason.message : "产物重生成失败"),
  });

  if (!run.data) return <p className="text-muted-foreground p-8">加载研究运行…</p>;
  const analysisUsage = llmUsageTotal(run.data.progress, "analysis_llm_usage");
  const relevanceUsage = llmUsageTotal(run.data.progress, "relevance_llm_usage");
  const synthesisUsage = llmUsageTotal(run.data.progress, "synthesis_llm_usage");
  const usageSummaries: Array<[string, LlmUsageTotal | null]> = [
    ["Facet 相关性判定", relevanceUsage],
    ["论文分析（含失败重试）", analysisUsage],
    ["综合生成", synthesisUsage],
  ];
  return (
    <div className="space-y-6 pb-10">
      <RunEventSync runId={runId} />
      <PageHeader
        eyebrow={`Run ${runId.slice(0, 8)}`}
        title="论文调研运行"
        description={`Protocol ${run.data.protocol_hash.slice(0, 20)}… · project ${projectId.slice(0, 8)}`}
        actions={
          <>
            <Badge variant={run.data.state.includes("FAILED") ? "destructive" : "outline"}>
              {run.data.state}
            </Badge>
            {(run.data.state === "FAILED_RETRYABLE" || run.data.state === "PAUSED") && (
              <Button variant="outline" onClick={() => action.mutate("resume")}>
                <PlayCircle className="h-4 w-4" />
                恢复
              </Button>
            )}
            {!run.data.state.includes("COMPLETED") &&
              !run.data.state.includes("CANCEL") &&
              run.data.state !== "PAUSED" &&
              run.data.state !== "FAILED_RETRYABLE" && (
                <>
                  <Button variant="outline" onClick={() => action.mutate("pause")}>
                    <PauseCircle className="h-4 w-4" />
                    暂停
                  </Button>
                  <Button variant="outline" onClick={() => action.mutate("cancel")}>
                    <XCircle className="h-4 w-4" />
                    取消
                  </Button>
                </>
              )}
          </>
        }
      />
      <ShortfallCard
        run={run.data}
        busy={action.isPending}
        onAccept={() => action.mutate("accept")}
        onCancel={() => action.mutate("cancel")}
      />
      {actionError && (
        <p className="text-destructive border-destructive/30 rounded-lg border p-3 text-sm">
          {actionError}
        </p>
      )}
      <Card>
        <CardHeader>
          <CardTitle>可恢复状态时间线</CardTitle>
        </CardHeader>
        <CardContent>
          <ResearchStageTimeline state={run.data.state} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>候选损失漏斗</CardTitle>
        </CardHeader>
        <CardContent>
          <ResearchFunnel run={run.data} />
        </CardContent>
      </Card>
      {(relevanceUsage || analysisUsage || synthesisUsage) && (
        <Card>
          <CardHeader>
            <CardTitle>LLM 用量审计</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-3">
            {usageSummaries.map(([label, usage]) =>
              usage ? (
                <div key={String(label)} className="rounded-lg border p-3 text-sm">
                  <p className="font-medium">{String(label)}</p>
                  <p>
                    {usage.requests.toLocaleString()} requests ·{" "}
                    {usage.total_tokens.toLocaleString()} tokens
                  </p>
                  <p className="text-muted-foreground text-xs">
                    input {usage.input_tokens.toLocaleString()} / output{" "}
                    {usage.output_tokens.toLocaleString()}
                  </p>
                  <p className="text-muted-foreground text-xs">
                    cost{" "}
                    {usage.cost_status === "REPORTED"
                      ? `$${usage.cost_usd}`
                      : "UNAVAILABLE（网关未提供可靠成本）"}
                  </p>
                </div>
              ) : null,
            )}
          </CardContent>
        </Card>
      )}
      <div
        className={
          selectedWorkId ? "grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(22rem,.8fr)]" : "block"
        }
      >
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>候选论文</CardTitle>
              <span className="text-muted-foreground text-xs">
                {candidates.data?.total ?? 0} total
              </span>
            </div>
          </CardHeader>
          <CardContent>
            <CandidateTable
              candidates={candidates.data?.items ?? []}
              selectedWorkId={selectedWorkId}
              onSelect={(workId) => {
                setFeedbackStatus(null);
                selectWork(workId);
              }}
            />
          </CardContent>
        </Card>
        {selectedWorkId && (
          <PaperDetailPanel
            detail={paper.data}
            loading={paper.isLoading}
            onClose={() => {
              setFeedbackStatus(null);
              selectWork(null);
            }}
            onReanalyze={() => reanalyze.mutate()}
            onRelevanceFeedback={(decision) => relevanceFeedback.mutate(decision)}
            feedbackBusy={relevanceFeedback.isPending}
            feedbackStatus={feedbackStatus}
          />
        )}
      </div>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <CardTitle>确定性产物</CardTitle>
            {run.data.progress.artifacts_require_regeneration === true && (
              <Button
                variant="outline"
                disabled={regenerate.isPending}
                onClick={() => regenerate.mutate()}
              >
                <RefreshCcw className="h-4 w-4" />
                {regenerate.isPending ? "正在提交…" : "生成新代产物"}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {run.data.progress.catalog_scope === "metadata_only" && (
            <p className="text-muted-foreground mb-4 text-sm">
              当前为元数据论文集：已完成来源核验、去重、硬约束与相关性排序；未获取 PDF，未执行证据审计或深度分析。
            </p>
          )}
          <ArtifactDownloads runId={runId} artifacts={artifacts.data ?? []} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>人工金标准评测看板</CardTitle>
        </CardHeader>
        <CardContent>
          <EvaluationControls projectId={projectId} runId={runId} />
          <div className="border-border mt-6 border-t pt-6">
            <EvaluationDashboard reports={evaluations.data ?? []} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
