"use client";

import { Component, FormEvent, useCallback, useEffect, useMemo, useRef, useState, type ErrorInfo, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Database, FileText, RefreshCw, Search, Square } from "lucide-react";

import { Alert, AlertDescription, AlertTitle, Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label, Textarea } from "@/components/ui";
import { MarkdownContent } from "@/components/chat/markdown-content";
import { literatureResearchApi } from "@/lib/literature-research-api";
import { createUuid } from "@/lib/client-id";
import { WS_URL } from "@/lib/constants";
import { getErrorMessage, setUrlParam } from "@/lib/utils";
import { useWebSocket } from "@/hooks/use-websocket";
import { useAuthStore } from "@/stores";
import type { LocalLibraryStatus, LocalPaper, LocalPaperAnalysisJob, LocalPaperSearchResponse } from "@/types/literature-research";

const terminal = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]);
const ANALYSIS_JOB_QUERY_KEY = "localAnalysisJob";
const ANALYSIS_JOB_STORAGE_KEY = "academic-research:local-paper-analysis-job";
const SEARCH_RESULT_STORAGE_KEY = "academic-research:local-paper-search-result";
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/**
 * A malformed historical report or a failed optional markdown chunk must not
 * take down the whole research page. The durable download remains available,
 * and the plain-text fallback is deliberately local to the report surface.
 */
class AnalysisReportBoundary extends Component<{ children: ReactNode; content: string }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // Do not render provider responses or exception details into the page.
    console.warn("Local paper analysis rich report rendering failed; using plain-text fallback.");
  }

  render() {
    if (this.state.failed) {
      return <pre className="max-h-96 overflow-auto whitespace-pre-wrap">{this.props.content}</pre>;
    }
    return this.props.children;
  }
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function download(format: "markdown" | "csv" | "bibtex" | "opml", query: string) {
  return fetch("/api/research/local-library/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ format, query, limit: 100 }) })
    .then(async (response) => {
      if (!response.ok) throw new Error("导出失败，请确认管理员权限与同步状态。");
      const blob = await response.blob(); const url = URL.createObjectURL(blob); const a = document.createElement("a");
      a.href = url; a.download = `local-paper-library.${format === "markdown" ? "md" : format === "bibtex" ? "bib" : format}`; a.click(); URL.revokeObjectURL(url);
    });
}

function PaperRow({ paper }: { paper: LocalPaper }) {
  return <article className="rounded-lg border p-3 text-sm"><div className="flex items-start justify-between gap-2"><h4 className="font-medium">{paper.title}</h4><Badge variant="outline">{paper.source_kind.toUpperCase()}</Badge></div><p className="text-muted-foreground mt-1 text-xs">{paper.authors.join("; ")} · {paper.publication_year ?? "年份未知"}</p>{paper.evidence.map((e) => <blockquote key={`${e.page_number}-${e.chunk_index}`} className="mt-2 border-l-2 pl-2 text-xs">p.{e.page_number}: {e.text}</blockquote>)}</article>;
}

export function analysisStageLabel(job: LocalPaperAnalysisJob, liveStage: string) {
  switch (job.status) {
    case "COMPLETED": return "分析完成";
    case "PARTIAL": return "部分完成";
    case "FAILED": return "分析失败";
    case "CANCELLED": return "已取消";
  }
  if (job.execution_mode === "background" && ["queued", "in_progress"].includes(job.provider_status ?? "")) {
    return job.provider_status === "queued" ? "模型服务排队中" : "模型正在分析";
  }
  if (job.status === "RETRIEVING") return "正在检索论文";
  if (job.stage === "EVIDENCE_READY") return "正在准备证据";
  if (job.status === "SYNTHESIZING") return "正在进行跨论文综合";
  if (job.status === "RENDERING") return "正在生成报告";
  if (job.status === "ANALYZING" && job.stage_total > 1) return `正在分析第 ${Math.min(job.stage_index + 1, job.stage_total - 1)}/${job.stage_total - 1} 篇论文`;
  return liveStage;
}

export function LocalPaperLibraryWorkbench() {
  const user = useAuthStore((s) => s.user); const accessToken = useAuthStore((s) => s.accessToken); const qc = useQueryClient();
  const [query, setQuery] = useState(""); const [limit, setLimit] = useState(10); const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<"focused" | "comparative" | "comprehensive">("focused"); const [format, setFormat] = useState<"markdown" | "opml">("markdown");
  const [jobId, setJobId] = useState<string | null>(null); const [result, setResult] = useState<string | null>(null); const [searchResult, setSearchResult] = useState<LocalPaperSearchResponse | null>(null); const [stage, setStage] = useState("未开始"); const [message, setMessage] = useState<string | null>(null); const syncSequence = useRef(0);
  useEffect(() => {
    // A job is durable in PostgreSQL; retaining only an in-memory id made a
    // browser refresh hide a still-running or completed audit record.
    const fromUrl = new URLSearchParams(window.location.search).get(ANALYSIS_JOB_QUERY_KEY);
    const fromStorage = window.sessionStorage.getItem(ANALYSIS_JOB_STORAGE_KEY);
    const candidate = fromUrl ?? fromStorage;
    if (candidate && UUID_RE.test(candidate)) setJobId(candidate);
    try {
      const savedSearch = window.sessionStorage.getItem(SEARCH_RESULT_STORAGE_KEY);
      if (savedSearch) {
        const parsed = JSON.parse(savedSearch) as LocalPaperSearchResponse;
        if (Array.isArray(parsed.items) && typeof parsed.total === "number") setSearchResult(parsed);
      }
    } catch {
      window.sessionStorage.removeItem(SEARCH_RESULT_STORAGE_KEY);
    }
  }, []);
  const status = useQuery({ queryKey: ["local-paper-library", "status"], queryFn: literatureResearchApi.localLibraryStatus, retry: false, refetchInterval: (q) => ["RUNNING", "QUEUED"].includes(q.state.data?.latest_sync?.status ?? "") ? 3000 : false });
  const jobQuery = useQuery({ queryKey: ["local-paper-analysis", jobId], queryFn: () => literatureResearchApi.localPaperAnalysisJob(jobId!), enabled: Boolean(jobId), refetchInterval: (q) => q.state.data && !terminal.has(q.state.data.status) ? 2000 : false });
  const syncRunId = status.data?.latest_sync?.id; const syncing = ["RUNNING", "QUEUED"].includes(status.data?.latest_sync?.status ?? "");
  const protocols = useMemo(() => accessToken ? [`access_token.${accessToken}`, "research"] : undefined, [accessToken]);
  const onSync = useCallback((event: MessageEvent) => { try { const e = JSON.parse(String(event.data)) as { type?: string; data?: { sync_run_id?: string; status?: string; summary_json?: Record<string, unknown> } }; const data = e.data; if (e.type !== "local_paper_sync_event" || !data || data.sync_run_id !== syncRunId) return; const sequence = Number(data.summary_json?.sequence ?? 0); if (sequence <= syncSequence.current) return; syncSequence.current = sequence; qc.setQueryData<LocalLibraryStatus>(["local-paper-library", "status"], (previous) => previous ? { ...previous, latest_sync: previous.latest_sync ? { ...previous.latest_sync, status: data.status ?? previous.latest_sync.status, summary_json: data.summary_json ?? previous.latest_sync.summary_json } : previous.latest_sync } : previous); } catch { /* Ignore malformed transient socket payloads. */ } }, [qc, syncRunId]);
  const { connect: connectSync, disconnect: disconnectSync } = useWebSocket({ url: `${WS_URL}/api/v1/research/local-library/sync/${syncRunId ?? "inactive"}/stream?after_sequence=0`, protocols, onMessage: onSync });
  useEffect(() => { if (!syncing || !syncRunId) return; connectSync(); return disconnectSync; }, [connectSync, disconnectSync, syncRunId, syncing]);
  const onAnalysis = useCallback((event: MessageEvent) => { try { const e = JSON.parse(String(event.data)) as { type?: string; data?: { job_id?: string; event_type?: string; status?: string } }; const data = e.data; if (e.type !== "local_paper_analysis_event" || !data || data.job_id !== jobId) return; setStage(data.event_type ?? data.status ?? "处理中"); void qc.invalidateQueries({ queryKey: ["local-paper-analysis", jobId] }); } catch { /* HTTP polling remains the recovery path for malformed socket data. */ } }, [jobId, qc]);
  const { connect: connectAnalysis, disconnect: disconnectAnalysis } = useWebSocket({ url: `${WS_URL}/api/v1/research/local-library/analysis-jobs/${jobId ?? "inactive"}/stream?after_sequence=0`, protocols, onMessage: onAnalysis });
  useEffect(() => { if (!jobId || terminal.has(jobQuery.data?.status ?? "")) return; connectAnalysis(); return disconnectAnalysis; }, [connectAnalysis, disconnectAnalysis, jobId, jobQuery.data?.status]);
  useEffect(() => {
    const job = jobQuery.data;
    if (!job) return;
    if (terminal.has(job.status)) setStage(job.status);
    if (!["COMPLETED", "PARTIAL"].includes(job.status)) return;
    // Render the durable job response first.  A transient BFF/object-store
    // failure must never turn a completed analysis into a blank page.
    const jobResult = record(job.result);
    const preview = jobResult.content_preview;
    if (typeof preview === "string" && preview) setResult(preview);
    if (jobResult.content_preview_truncated === true || !preview) {
      void fetch(`/api/research/local-library/analysis-jobs/${job.id}/artifact`)
        .then(async (response) => {
          if (!response.ok) throw new Error("分析产物读取失败");
          return response.text();
        })
        .then(setResult)
        .catch((error) => setMessage(getErrorMessage(error)));
    }
  }, [jobQuery.data]);
  const sync = useMutation({ mutationFn: literatureResearchApi.syncLocalLibrary, onSuccess: () => { setMessage("同步任务已提交。"); void qc.invalidateQueries({ queryKey: ["local-paper-library", "status"] }); }, onError: (e) => setMessage(getErrorMessage(e)) });
  const search = useMutation({ mutationFn: (body: { query: string; limit: number }) => literatureResearchApi.searchLocalLibrary(body), onSuccess: (data) => { setSearchResult(data); window.sessionStorage.setItem(SEARCH_RESULT_STORAGE_KEY, JSON.stringify(data)); }, onError: (e) => setMessage(getErrorMessage(e)) });
  const analyze = useMutation({ mutationFn: literatureResearchApi.createLocalPaperAnalysis, onSuccess: (job) => { window.sessionStorage.setItem(ANALYSIS_JOB_STORAGE_KEY, job.id); setUrlParam(ANALYSIS_JOB_QUERY_KEY, job.id); setJobId(job.id); setResult(null); setStage(job.status); }, onError: (e) => setMessage(getErrorMessage(e)) });
  const runSearch = (e: FormEvent) => { e.preventDefault(); setMessage(null); search.mutate({ query, limit }); };
  const runAnalysis = (e: FormEvent) => { e.preventDefault(); const ids = searchResult?.items.map((p) => p.id); if (!ids?.length && !query.trim()) { setMessage("请先检索论文，或填写检索主题。"); return; } analyze.mutate({ question, query: query.trim() || undefined, paper_ids: ids, limit: Math.min(50, ids?.length || limit), mode, output_format: format, client_request_id: createUuid() }); };
  const job = jobQuery.data as LocalPaperAnalysisJob | undefined; const jobResult = record(job?.result); const quarantine = Array.isArray(status.data?.quarantine) ? status.data.quarantine : []; const isAdmin = user?.is_app_admin === true || user?.role === "admin";
  const analysisInFlight = Boolean(jobId) && (!job || !terminal.has(job.status));
  return <Card className="border-primary/30"><CardHeader><CardTitle className="flex items-center gap-2"><Database className="size-5" />本地论文库（Zotero）</CardTitle><p className="text-muted-foreground text-sm">检索与问答已合并为可恢复、可审计的论文深度分析；仅使用本地页码证据。</p></CardHeader><CardContent className="space-y-4">
    {message && <Alert variant="warning"><AlertCircle /><AlertTitle>本地库提示</AlertTitle><AlertDescription>{message}</AlertDescription></Alert>}
    <div className="flex flex-wrap items-center gap-3 rounded-lg bg-muted/50 p-3 text-sm"><Badge>{status.data?.status ?? "读取状态中"}</Badge><span>已登记 {status.data?.catalogued_papers ?? 0} 篇</span><span>可检索 {status.data?.searchable_papers ?? 0} 篇</span><span>待重建 {status.data?.stale_indexed_papers ?? 0} 篇</span><Button size="sm" variant="outline" onClick={() => sync.mutate()} disabled={!isAdmin || sync.isPending || syncing}><RefreshCw className="mr-1 size-4" />手动同步/增量重建</Button></div>
    {status.data?.latest_sync && <p className="text-muted-foreground text-xs">最近同步：{status.data.latest_sync.status} · {Number(status.data.latest_sync.summary_json.processed ?? 0)}/{Number(status.data.latest_sync.summary_json.total_bibtex_entries ?? 0)} · 重建 {Number(status.data.latest_sync.summary_json.indexed ?? 0)} · 错误 {Number(status.data.latest_sync.summary_json.errors ?? 0)}</p>}
    <form onSubmit={runSearch} className="space-y-2"><div className="flex gap-2"><Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="主题、关键词、作者、DOI、年份或 BibTeX 类型" /><Button disabled={search.isPending}><Search className="mr-1 size-4" />检索</Button></div><div className="flex items-center gap-2"><Label htmlFor="search-limit" className="text-xs">返回数量</Label><Input id="search-limit" type="number" min={1} max={50} value={limit} onChange={(e) => setLimit(Math.min(50, Math.max(1, Number(e.target.value))))} className="w-20 text-xs" /></div></form>
    {searchResult && <div className="space-y-2"><p className="text-sm">命中 {searchResult.total} 篇，展示 {searchResult.items.length} 篇；检索追溯 ID：{searchResult.retrieval_run_id ?? "—"}</p>{searchResult.items.map((paper) => <PaperRow key={paper.id} paper={paper} />)}<div className="flex flex-wrap gap-2">{(["markdown", "csv", "bibtex", "opml"] as const).map((f) => <Button key={f} size="sm" variant="outline" type="button" onClick={() => void download(f, query).catch((e) => setMessage(getErrorMessage(e)))}><FileText className="mr-1 size-3" />{f.toUpperCase()}</Button>)}</div></div>}
    <div className="rounded-lg border p-3 space-y-3"><h3 className="text-sm font-medium">基于论文的深度分析</h3><form onSubmit={runAnalysis} className="space-y-2"><Textarea value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="例如：上述论文中的语义编码器如何设计？" /><div className="flex flex-wrap gap-2"><select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} className="border rounded px-2 py-1 text-xs"><option value="focused">聚焦回答</option><option value="comparative">横向对比</option><option value="comprehensive">完整综述</option></select><select value={format} onChange={(e) => setFormat(e.target.value as typeof format)} className="border rounded px-2 py-1 text-xs"><option value="markdown">Markdown</option><option value="opml">OPML</option></select><Button type="submit" disabled={analyze.isPending || analysisInFlight || question.trim().length < 3}>{analyze.isPending ? "提交中…" : analysisInFlight ? "分析处理中…" : "开始分析"}</Button></div></form>
    {job && <div className="rounded bg-muted p-3 text-xs space-y-2"><p>任务 {job.id} · <strong>{job.status}</strong> · 阶段：{analysisStageLabel(job, stage)} · 检索：{job.retrieval_run_id ?? "等待中"}</p>{job.status === "PARTIAL" && <p className="text-amber-700">部分论文未能完成分析；系统已保留成功论文的本地页码证据与部分结果。</p>}{!terminal.has(job.status) && <Button size="sm" variant="outline" onClick={() => void literatureResearchApi.cancelLocalPaperAnalysis(job.id)}><Square className="mr-1 size-3" />取消</Button>}{job.error_message && <p className="text-destructive">{job.error_message}</p>}{typeof jobResult.artifact_sha256 === "string" && <Button size="sm" variant="outline" onClick={() => window.open(`/api/research/local-library/analysis-jobs/${job.id}/artifact`, "_blank")}><FileText className="mr-1 size-3" />下载完整产物</Button>}{result && <>{format === "markdown" ? <AnalysisReportBoundary content={result}><MarkdownContent content={result} /></AnalysisReportBoundary> : <pre className="max-h-96 overflow-auto whitespace-pre-wrap">{result}</pre>}</>}{["COMPLETED", "PARTIAL"].includes(job.status) && !result && <p className="text-muted-foreground">分析已结束，正在恢复报告…</p>}</div>}</div>
    {quarantine.length ? <details className="text-xs"><summary>查看最近待核验/跳过项</summary><ul className="mt-2 list-disc space-y-1 pl-5">{quarantine.slice(0, 20).map((item, i) => <li key={`${item.item_kind}-${i}`}>{item.item_kind}: {item.relative_path ?? item.citekey ?? "—"} · {item.detail}</li>)}</ul></details> : null}
  </CardContent></Card>;
}
