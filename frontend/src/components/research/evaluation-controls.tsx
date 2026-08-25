"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Plus } from "lucide-react";
import { toast } from "sonner";

import {
  Badge,
  Button,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from "@/components/ui";
import { literatureResearchApi } from "@/lib/literature-research-api";
import { qk } from "@/lib/query-keys";
import { formatDateTime, getErrorMessage } from "@/lib/utils";
import type { EvaluationDatasetCreateInput } from "@/types/literature-research";

const DEFAULT_DATASET = JSON.stringify(
  {
    name: "人工金标准集",
    version: "1",
    description: "待双人标注和仲裁的项目金标准",
    status: "DRAFT",
    cases: [
      {
        case_id: "case-001",
        title: "请替换为真实论文标题",
        doi: null,
        relevant: true,
        relevance_grade: 3,
        allowed_quote_sha256: [],
        expected_numeric_values: [],
      },
    ],
    observations: [],
  },
  null,
  2,
);

function parseDataset(text: string, projectId: string): EvaluationDatasetCreateInput {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("金标准数据集必须是 JSON 对象");
  }
  return { ...(value as Omit<EvaluationDatasetCreateInput, "project_id">), project_id: projectId };
}

export function EvaluationControls({ projectId, runId }: { projectId: string; runId: string }) {
  const queryClient = useQueryClient();
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [datasetJson, setDatasetJson] = useState(DEFAULT_DATASET);

  const datasets = useQuery({
    queryKey: qk.literatureResearch.evaluationDatasets(projectId),
    queryFn: () => literatureResearchApi.evaluationDatasets(projectId),
  });
  const evaluatable = useMemo(
    () => datasets.data?.filter((item) => item.status !== "DRAFT") ?? [],
    [datasets.data],
  );

  useEffect(() => {
    if (!selectedDatasetId && evaluatable[0]) {
      setSelectedDatasetId(evaluatable[0].id);
    }
  }, [evaluatable, selectedDatasetId]);

  const createDataset = useMutation({
    mutationFn: (body: EvaluationDatasetCreateInput) =>
      literatureResearchApi.createEvaluationDataset(projectId, body),
    onSuccess: async (created) => {
      toast.success(`${created.name} v${created.version} 已创建`);
      setDatasetJson(DEFAULT_DATASET);
      if (created.status !== "DRAFT") setSelectedDatasetId(created.id);
      await queryClient.invalidateQueries({
        queryKey: qk.literatureResearch.evaluationDatasets(projectId),
      });
    },
    onError: (error) => toast.error(getErrorMessage(error, "金标准数据集创建失败")),
  });

  const evaluate = useMutation({
    mutationFn: () => literatureResearchApi.evaluateRun(runId, selectedDatasetId),
    onSuccess: async (report) => {
      toast.success(report.passed ? "评测完成：PASS" : "评测完成：存在失败或样本不足");
      await queryClient.invalidateQueries({
        queryKey: qk.literatureResearch.evaluations(runId),
      });
    },
    onError: (error) => toast.error(getErrorMessage(error, "运行评测失败")),
  });

  const submitDataset = () => {
    try {
      createDataset.mutate(parseDataset(datasetJson, projectId));
    } catch (error) {
      toast.error(getErrorMessage(error, "金标准 JSON 无效"));
    }
  };

  return (
    <div className="space-y-5">
      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
        <div className="space-y-2">
          <Label>已裁决或外部 benchmark 数据集</Label>
          <Select value={selectedDatasetId} onValueChange={setSelectedDatasetId}>
            <SelectTrigger>
              <SelectValue placeholder="当前没有可执行评测的数据集" />
            </SelectTrigger>
            <SelectContent>
              {evaluatable.map((dataset) => (
                <SelectItem key={dataset.id} value={dataset.id}>
                  {dataset.name} v{dataset.version} · {dataset.status} · n={dataset.case_count}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="outline"
          disabled={!selectedDatasetId || evaluate.isPending}
          onClick={() => evaluate.mutate()}
        >
          <FlaskConical className="h-4 w-4" />
          {evaluate.isPending ? "评测中…" : "针对当前 run 运行评测"}
        </Button>
      </div>

      {datasets.data?.length ? (
        <div className="flex flex-wrap gap-2">
          {datasets.data.map((dataset) => (
            <Badge key={dataset.id} variant={dataset.status === "DRAFT" ? "outline" : "default"}>
              {dataset.name} v{dataset.version} · {dataset.status} · n={dataset.case_count} ·{" "}
              {formatDateTime(dataset.created_at)}
            </Badge>
          ))}
        </div>
      ) : (
        <p className="text-muted-foreground text-sm">该项目尚无金标准数据集。</p>
      )}

      <details className="border-border rounded-lg border p-4">
        <summary className="cursor-pointer text-sm font-medium">创建版本化金标准数据集</summary>
        <div className="mt-4 space-y-3">
          <p className="text-muted-foreground text-xs">
            DRAFT 可以保存但不能评测。ADJUDICATED 必须提供至少两名标注者的 provenance；
            EXTERNAL_BENCHMARK 也必须声明来源、许可、覆盖范围和局限。不得把合成样本标成真实 gold。
          </p>
          <Textarea
            value={datasetJson}
            onChange={(event) => setDatasetJson(event.target.value)}
            className="min-h-80 font-mono text-xs"
            aria-label="Gold dataset JSON"
          />
          <div className="flex justify-end">
            <Button disabled={createDataset.isPending} onClick={submitDataset}>
              <Plus className="h-4 w-4" />
              {createDataset.isPending ? "创建中…" : "创建不可变数据集版本"}
            </Button>
          </div>
        </div>
      </details>
    </div>
  );
}
