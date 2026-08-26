"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, LockKeyhole } from "lucide-react";

import { PageHeader } from "@/components/dashboard/page-header";
import { LocalPaperLibraryWorkbench, ResearchOrganizationSwitcher } from "@/components/research";
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Checkbox,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from "@/components/ui";
import { literatureResearchApi } from "@/lib/literature-research-api";
import { createClientId } from "@/lib/client-id";
import { ROUTES } from "@/lib/constants";
import type {
  ResearchExecutionMode,
  ResearchProject,
  ResearchProtocolVersion,
} from "@/types/literature-research";
import { useResearchOrganizationStore } from "@/stores";

const RESEARCH_DRAFT_SESSION_KEY = "academic-research:l1-draft-session";
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function dateMonthsAgo(months: number) {
  const date = new Date();
  date.setMonth(date.getMonth() - months);
  return date.toISOString().slice(0, 10);
}

export default function NewResearchPage() {
  const router = useRouter();
  const activeOrganizationId = useResearchOrganizationStore((state) => state.activeOrganizationId);
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [definition, setDefinition] = useState("");
  const [dateFrom, setDateFrom] = useState(dateMonthsAgo(3));
  const [dateTo, setDateTo] = useState(new Date().toISOString().slice(0, 10));
  const [targetCount, setTargetCount] = useState(20);
  const [llmMaxRequests, setLlmMaxRequests] = useState(64);
  const [llmMaxInputTokens, setLlmMaxInputTokens] = useState(1_500_000);
  const [llmMaxOutputTokens, setLlmMaxOutputTokens] = useState(100_000);
  const [llmMaxCostUsd, setLlmMaxCostUsd] = useState("");
  const [journal, setJournal] = useState(true);
  const [conference, setConference] = useState(true);
  const [constraintsJson, setConstraintsJson] = useState("[]");
  const [executionMode, setExecutionMode] = useState<ResearchExecutionMode>("search_only");
  const [project, setProject] = useState<ResearchProject | null>(null);
  const [draft, setDraft] = useState<ResearchProtocolVersion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionSavedAt, setSessionSavedAt] = useState<string | null>(null);
  const restoredSession = useRef(false);
  useEffect(() => {
    const existing = window.localStorage.getItem(RESEARCH_DRAFT_SESSION_KEY);
    // The L1 API is deliberately UUID-keyed.  Replace the old human-readable
    // client id once so existing browsers stop issuing a permanent 422.
    const id = existing && UUID_RE.test(existing) ? existing : crypto.randomUUID();
    if (id !== existing) window.localStorage.setItem(RESEARCH_DRAFT_SESSION_KEY, id);
    setSessionId(id);
  }, []);
  const sessionMemory = useQuery({
    queryKey: ["literature-research", "l1-session", sessionId],
    queryFn: () => literatureResearchApi.sessionMemory(sessionId!),
    enabled: Boolean(sessionId),
    retry: false,
  });
  const existingProjects = useQuery({
    queryKey: ["literature-research", "draft-projects", activeOrganizationId ?? "personal"],
    queryFn: () => literatureResearchApi.listProjects(activeOrganizationId),
  });
  const readiness = useQuery({
    queryKey: ["research-readiness"],
    queryFn: literatureResearchApi.readiness,
    refetchInterval: 30_000,
    retry: false,
  });
  const modeAvailable =
    executionMode === "validate_only" ||
    (executionMode === "search_only" && readiness.data?.capabilities.search_only === true) ||
    (executionMode === "full_research" && readiness.data?.capabilities.full_research === true);

  useEffect(() => {
    if (!sessionMemory.isSuccess || restoredSession.current) return;
    const memory = sessionMemory.data;
    if (memory?.project_id && existingProjects.data === undefined) return;
    const slots = memory?.draft_slots ?? {};
    const text = (key: string) => (typeof slots[key] === "string" ? slots[key] : undefined);
    const number = (key: string) =>
      typeof slots[key] === "number" && Number.isFinite(slots[key])
        ? (slots[key] as number)
        : undefined;
    const boolean = (key: string) =>
      typeof slots[key] === "boolean" ? (slots[key] as boolean) : undefined;
    setTitle(text("title") ?? title);
    setTopic(text("topic") ?? topic);
    setDefinition(text("definition") ?? definition);
    setDateFrom(text("date_from") ?? dateFrom);
    setDateTo(text("date_to") ?? dateTo);
    setTargetCount(number("target_count") ?? targetCount);
    setLlmMaxRequests(number("llm_max_requests") ?? llmMaxRequests);
    setLlmMaxInputTokens(number("llm_max_input_tokens") ?? llmMaxInputTokens);
    setLlmMaxOutputTokens(number("llm_max_output_tokens") ?? llmMaxOutputTokens);
    setLlmMaxCostUsd(text("llm_max_cost_usd") ?? llmMaxCostUsd);
    setJournal(boolean("journal") ?? journal);
    setConference(boolean("conference") ?? conference);
    setConstraintsJson(text("constraints_json") ?? constraintsJson);
    const restoredMode = text("execution_mode");
    if (["validate_only", "search_only", "full_research"].includes(restoredMode ?? "")) {
      setExecutionMode(restoredMode as ResearchExecutionMode);
    }
    if (memory?.project_id) {
      const restoredProject = existingProjects.data?.find((item) => item.id === memory.project_id);
      if (restoredProject) setProject(restoredProject);
    }
    setSessionSavedAt(memory?.updated_at ?? null);
    restoredSession.current = true;
  }, [
    conference,
    constraintsJson,
    dateFrom,
    dateTo,
    definition,
    existingProjects.data,
    journal,
    llmMaxCostUsd,
    llmMaxInputTokens,
    llmMaxOutputTokens,
    llmMaxRequests,
    sessionMemory.data,
    sessionMemory.isSuccess,
    targetCount,
    title,
    topic,
  ]);

  useEffect(() => {
    if (!sessionId || !sessionMemory.isSuccess || !restoredSession.current) return;
    const timer = window.setTimeout(() => {
      void literatureResearchApi
        .saveSessionMemory(sessionId, {
          ...(project ? { project_id: project.id } : {}),
          draft_slots: {
            title,
            topic,
            definition,
            date_from: dateFrom,
            date_to: dateTo,
            target_count: targetCount,
            llm_max_requests: llmMaxRequests,
            llm_max_input_tokens: llmMaxInputTokens,
            llm_max_output_tokens: llmMaxOutputTokens,
            llm_max_cost_usd: llmMaxCostUsd,
            journal,
            conference,
            constraints_json: constraintsJson,
            execution_mode: executionMode,
          },
          missing_slots: [...(title.trim() ? [] : ["title"]), ...(topic.trim() ? [] : ["topic"])],
          source_message_ids: [],
        })
        .then((saved) => setSessionSavedAt(saved.updated_at))
        .catch(() => setSessionSavedAt("error"));
    }, 800);
    return () => window.clearTimeout(timer);
  }, [
    conference,
    constraintsJson,
    dateFrom,
    dateTo,
    definition,
    executionMode,
    journal,
    llmMaxCostUsd,
    llmMaxInputTokens,
    llmMaxOutputTokens,
    llmMaxRequests,
    project,
    sessionId,
    sessionMemory.isSuccess,
    targetCount,
    title,
    topic,
  ]);

  const compile = useMutation({
    mutationFn: async (useAdvice: boolean) => {
      setError(null);
      const constraints = JSON.parse(constraintsJson) as unknown[];
      const created =
        project ??
        (await literatureResearchApi.createProject(
          {
            title,
            description: definition,
            ...(activeOrganizationId ? { organization_id: activeOrganizationId } : {}),
          },
          activeOrganizationId,
        ));
      setProject(created);
      const body = {
        topic,
        topic_definition: definition,
        date_from: dateFrom,
        date_to: dateTo,
        allowed_types: [
          journal ? "journal_article" : null,
          conference ? "conference_paper" : null,
        ].filter(Boolean),
        allowed_languages: ["en"],
        target_count: targetCount,
        constraints,
        shortfall_action: "ask_user_before_relaxation",
        llm_budget: {
          max_requests: llmMaxRequests,
          max_input_tokens: llmMaxInputTokens,
          max_output_tokens: llmMaxOutputTokens,
          max_total_tokens: llmMaxInputTokens + llmMaxOutputTokens,
          ...(llmMaxCostUsd ? { max_cost_usd: llmMaxCostUsd } : {}),
        },
      };
      const protocol = useAdvice
        ? await literatureResearchApi.adviseAndCompileProtocol(created.id, body)
        : await literatureResearchApi.compileProtocol(created.id, body);
      return { created, protocol };
    },
    onSuccess: ({ created, protocol }) => {
      setProject(created);
      setDraft(protocol);
    },
    onError: (reason) => setError(reason instanceof Error ? reason.message : "协议编译失败"),
  });
  const approveAndRun = useMutation({
    mutationFn: async () => {
      if (!project || !draft) throw new Error("缺少协议草案");
      await literatureResearchApi.approveProtocol(project.id, draft.version, draft.protocol_hash);
      return literatureResearchApi.createRun(
        {
          project_id: project.id,
          protocol_version: draft.version,
          execution_mode: executionMode,
          force_refresh_sources: false,
          client_request_id: createClientId("protocol-run"),
        },
        project.organization_id ?? null,
      );
    },
    onSuccess: (run) => {
      window.localStorage.removeItem(RESEARCH_DRAFT_SESSION_KEY);
      router.push(ROUTES.RESEARCH_RUN(run.project_id, run.id));
    },
    onError: (reason) => setError(reason instanceof Error ? reason.message : "启动失败"),
  });

  function requestCompile(useAdvice: boolean) {
    if (!journal && !conference) return setError("至少选择一种论文类型");
    try {
      JSON.parse(constraintsJson);
      compile.mutate(useAdvice);
    } catch {
      setError("质量约束必须是有效 JSON 数组");
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    requestCompile(false);
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 pb-10">
      <PageHeader
        eyebrow="Compile → approve → run"
        title="新建论文调研"
        description="自然语言只负责表达意图；日期、论文类型和质量约束将被编译为可确认的不可变协议。"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={sessionSavedAt === "error" ? "destructive" : "outline"}>
              {sessionSavedAt === "error"
                ? "L1 草案保存失败"
                : sessionSavedAt
                  ? "L1 草案已保存（24h）"
                  : "正在恢复 L1 草案…"}
            </Badge>
            <ResearchOrganizationSwitcher />
          </div>
        }
      />
      <LocalPaperLibraryWorkbench />
      <Alert variant="warning">
        <LockKeyhole />
        <AlertTitle>数量是目标，质量是不可变可行域</AlertTitle>
        <AlertDescription>
          系统绝不会为了凑满目标数量自动降低日期、JIF/分区、会议规则或相关性门槛。
        </AlertDescription>
      </Alert>
      {!draft ? (
        <form onSubmit={onSubmit} className="grid gap-6 lg:grid-cols-[1.2fr_.8fr]">
          <Card>
            <CardHeader>
              <CardTitle>研究需求</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Label htmlFor="title">项目名称</Label>
                <Input
                  id="title"
                  required
                  minLength={3}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="topic">研究课题</Label>
                <Input
                  id="topic"
                  required
                  minLength={3}
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="definition">课题定义与核心问题</Label>
                <Textarea
                  id="definition"
                  rows={5}
                  value={definition}
                  onChange={(e) => setDefinition(e.target.value)}
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <Label htmlFor="date-from">起始日期（含）</Label>
                  <Input
                    id="date-from"
                    type="date"
                    value={dateFrom}
                    onChange={(e) => setDateFrom(e.target.value)}
                  />
                </div>
                <div>
                  <Label htmlFor="date-to">结束日期（含）</Label>
                  <Input
                    id="date-to"
                    type="date"
                    value={dateTo}
                    onChange={(e) => setDateTo(e.target.value)}
                  />
                </div>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>范围与质量门槛</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Label htmlFor="target">目标数量</Label>
                <Input
                  id="target"
                  type="number"
                  min={1}
                  max={200}
                  value={targetCount}
                  onChange={(e) => setTargetCount(Number(e.target.value))}
                />
              </div>
              <div className="space-y-2 rounded-lg border p-3">
                <Label>单次 LLM 操作硬上限</Label>
                <div className="grid gap-2 sm:grid-cols-2">
                  <div>
                    <Label htmlFor="llm-requests" className="text-xs">
                      最多请求数
                    </Label>
                    <Input
                      id="llm-requests"
                      type="number"
                      min={1}
                      max={256}
                      value={llmMaxRequests}
                      onChange={(e) => setLlmMaxRequests(Number(e.target.value))}
                    />
                  </div>
                  <div>
                    <Label htmlFor="llm-cost" className="text-xs">
                      最多美元（可选）
                    </Label>
                    <Input
                      id="llm-cost"
                      type="number"
                      min="0.000001"
                      step="0.01"
                      placeholder="不设置"
                      value={llmMaxCostUsd}
                      onChange={(e) => setLlmMaxCostUsd(e.target.value)}
                    />
                  </div>
                  <div>
                    <Label htmlFor="llm-input" className="text-xs">
                      最多输入 token
                    </Label>
                    <Input
                      id="llm-input"
                      type="number"
                      min={1000}
                      max={10_000_000}
                      value={llmMaxInputTokens}
                      onChange={(e) => setLlmMaxInputTokens(Number(e.target.value))}
                    />
                  </div>
                  <div>
                    <Label htmlFor="llm-output" className="text-xs">
                      最多输出 token
                    </Label>
                    <Input
                      id="llm-output"
                      type="number"
                      min={1000}
                      max={1_000_000}
                      value={llmMaxOutputTokens}
                      onChange={(e) => setLlmMaxOutputTokens(Number(e.target.value))}
                    />
                  </div>
                </div>
                <p className="text-muted-foreground text-xs">
                  该预算写入协议哈希。若设置美元上限而第三方网关不返回可靠成本，系统将
                  fail-closed；token 仍始终记录。
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="execution-mode">执行模式</Label>
                <Select
                  value={executionMode}
                  onValueChange={(value) => setExecutionMode(value as ResearchExecutionMode)}
                >
                  <SelectTrigger id="execution-mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="validate_only">仅验证协议</SelectItem>
                    <SelectItem
                      value="search_only"
                      disabled={readiness.data?.capabilities.search_only === false}
                    >
                      检索、严格筛选、排序与导出
                    </SelectItem>
                    <SelectItem
                      value="full_research"
                      disabled={readiness.data?.capabilities.full_research === false}
                    >
                      全文、证据与深度分析
                    </SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-muted-foreground text-xs">
                  {executionMode === "full_research"
                    ? readiness.data?.capabilities.full_research
                      ? `LLM 可用：${readiness.data.llm?.provider ?? "provider"} / ${readiness.data.llm?.model ?? "model"}`
                      : `LLM 当前不可用：${readiness.data?.llm?.detail ?? "凭据、网络或模型权限探测未通过"}`
                    : executionMode === "search_only"
                      ? "使用真实学术源与本地相关性模型，不调用 LLM、不获取 PDF；完成后导出 Markdown、CSV、BibTeX 与简化 OPML。"
                      : "只检查并冻结协议，不执行检索。"}
                </p>
              </div>
              <div className="space-y-2">
                <Label>论文类型</Label>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={journal}
                    onCheckedChange={(value) => setJournal(Boolean(value))}
                  />
                  期刊论文
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={conference}
                    onCheckedChange={(value) => setConference(Boolean(value))}
                  />
                  会议论文
                </label>
              </div>
              <div>
                <Label htmlFor="constraints">高级质量约束 JSON</Label>
                <Textarea
                  id="constraints"
                  className="font-mono text-xs"
                  rows={10}
                  value={constraintsJson}
                  onChange={(e) => setConstraintsJson(e.target.value)}
                />
                <p className="text-muted-foreground mt-1 text-xs">
                  JIF/CAS 只适用于期刊；会议需使用 rank/allowlist 独立规则。
                </p>
              </div>
              <div className="space-y-2">
                <Button className="w-full" disabled={compile.isPending}>
                  {compile.isPending ? "编译中…" : "确定性编译（不调用 LLM）"}
                </Button>
                <Button
                  className="w-full"
                  type="button"
                  variant="outline"
                  disabled={compile.isPending || readiness.data?.llm?.status !== "healthy"}
                  onClick={() => requestCompile(true)}
                >
                  AI 建议并编译（会调用 LLM）
                </Button>
                <p className="text-muted-foreground text-xs">
                  AI 仅补充课题定义、研究问题和必备 facet；日期、来源、类型、质量约束与预算
                  保持原值，生成结果仍需你明确批准。
                </p>
              </div>
            </CardContent>
          </Card>
        </form>
      ) : (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle>协议 v{draft.version}</CardTitle>
              <Badge
                variant={draft.protocol.ambiguity_status === "resolved" ? "default" : "destructive"}
              >
                {draft.protocol.ambiguity_status}
              </Badge>
            </div>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-4">
              <div className="rounded-lg border p-3">
                <p className="text-muted-foreground text-xs">Date</p>
                <p>
                  {draft.protocol.time_scope.from} → {draft.protocol.time_scope.to}
                </p>
              </div>
              <div className="rounded-lg border p-3">
                <p className="text-muted-foreground text-xs">Types</p>
                <p>{draft.protocol.document_scope.allowed_types.join(", ")}</p>
              </div>
              <div className="rounded-lg border p-3">
                <p className="text-muted-foreground text-xs">Quantity</p>
                <p>{draft.protocol.quantity_policy.target_count} / floor locked</p>
              </div>
              <div className="rounded-lg border p-3">
                <p className="text-muted-foreground text-xs">LLM budget / operation</p>
                <p>
                  {draft.protocol.llm_budget.max_requests} requests /{" "}
                  {draft.protocol.llm_budget.max_total_tokens.toLocaleString()} tokens
                </p>
                <p className="text-muted-foreground text-xs">
                  USD {draft.protocol.llm_budget.max_cost_usd ?? "not enforced"}
                </p>
              </div>
            </div>
            <div>
              <p className="text-muted-foreground mb-1 text-xs">Protocol hash</p>
              <code className="text-xs break-all">{draft.protocol_hash}</code>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-lg border p-3">
                <p className="text-muted-foreground mb-2 text-xs">研究问题</p>
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {draft.protocol.research_questions.map((question) => (
                    <li key={question}>{question}</li>
                  ))}
                </ul>
              </div>
              <div className="rounded-lg border p-3">
                <p className="text-muted-foreground mb-2 text-xs">必备主题 facet</p>
                <ul className="space-y-2 text-sm">
                  {draft.protocol.topic_model.must_have_facets.map((facet) => (
                    <li key={facet.facet_id}>
                      <span className="font-medium">{facet.name}</span>
                      <p className="text-muted-foreground text-xs">{facet.description}</p>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            {draft.protocol.draft_advice_provenance && (
              <div className="rounded-lg border p-3 text-sm">
                <p className="font-medium">AI 草案来源</p>
                <p className="text-muted-foreground text-xs break-all">
                  {draft.protocol.draft_advice_provenance.model_identifier} · prompt{" "}
                  {draft.protocol.draft_advice_provenance.prompt_version}
                </p>
                <p className="text-muted-foreground text-xs">
                  本次 {draft.protocol.draft_advice_provenance.llm_usage.total?.requests ?? 0}{" "}
                  次请求 /{" "}
                  {(
                    draft.protocol.draft_advice_provenance.llm_usage.total?.total_tokens ?? 0
                  ).toLocaleString()}{" "}
                  token；成本{" "}
                  {draft.protocol.draft_advice_provenance.llm_usage.total?.cost_usd ?? "不可用"}
                </p>
                {draft.protocol.draft_advice_provenance.memory_context && (
                  <p className="text-muted-foreground text-xs">
                    记忆召回 {draft.protocol.draft_advice_provenance.memory_context.retrieval_mode}
                    ：项目记忆{" "}
                    {
                      draft.protocol.draft_advice_provenance.memory_context.project_memory_ids
                        .length
                    }{" "}
                    条，画像 v
                    {draft.protocol.draft_advice_provenance.memory_context.profile_version ?? "无"}
                    ， 有效策略{" "}
                    {
                      Object.keys(
                        draft.protocol.draft_advice_provenance.memory_context.policy_versions,
                      ).length
                    }{" "}
                    个
                  </p>
                )}
              </div>
            )}
            {draft.protocol.issues.map((issue) => (
              <Alert key={issue.code} variant={issue.blocking ? "destructive" : "warning"}>
                <AlertTriangle />
                <AlertTitle>{issue.code}</AlertTitle>
                <AlertDescription>{issue.message}</AlertDescription>
              </Alert>
            ))}
            {draft.protocol.ambiguity_status === "resolved" && (
              <Alert variant="success">
                <CheckCircle2 />
                <AlertTitle>协议可执行</AlertTitle>
                <AlertDescription>
                  确认后该哈希被冻结；任何放宽都必须产生新版本并再次确认。
                </AlertDescription>
              </Alert>
            )}
            {!modeAvailable && (
              <Alert variant="warning">
                <AlertTriangle />
                <AlertTitle>所选模式暂不可用</AlertTitle>
                <AlertDescription>
                  {executionMode === "full_research"
                    ? "请配置所选 LLM provider 的凭据，并确认网络与模型权限探测通过。"
                    : "研究依赖尚未就绪，请查看系统健康状态。"}
                </AlertDescription>
              </Alert>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDraft(null)}>
                返回修改
              </Button>
              <Button
                disabled={
                  draft.protocol.ambiguity_status !== "resolved" ||
                  approveAndRun.isPending ||
                  !modeAvailable
                }
                onClick={() => approveAndRun.mutate()}
              >
                {approveAndRun.isPending ? "启动中…" : "批准协议并启动运行"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>操作失败</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
    </div>
  );
}
