"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Database, FileText, RefreshCw, Search } from "lucide-react";

import { Alert, AlertDescription, AlertTitle, Badge, Button, Card, CardContent, CardHeader, CardTitle, Input, Label, Textarea } from "@/components/ui";
import { MarkdownContent } from "@/components/chat/markdown-content";
import { literatureResearchApi } from "@/lib/literature-research-api";
import { WS_URL } from "@/lib/constants";
import { getErrorMessage } from "@/lib/utils";
import { useWebSocket } from "@/hooks/use-websocket";
import { useAuthStore } from "@/stores";
import type { LocalLibraryStatus, LocalPaper } from "@/types/literature-research";

function download(format: "markdown" | "csv" | "bibtex" | "opml", query: string) {
  return fetch("/api/research/local-library/export", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format, query, limit: 100 }),
  }).then(async (response) => {
    if (!response.ok) throw new Error("导出失败，请确认管理员权限与同步状态。");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    const extension = format === "markdown" ? "md" : format === "bibtex" ? "bib" : format;
    anchor.href = url; anchor.download = `local-paper-library.${extension}`; anchor.click();
    URL.revokeObjectURL(url);
  });
}

function PaperRow({ paper }: { paper: LocalPaper }) {
  return <article className="rounded-lg border p-3 text-sm">
    <div className="flex flex-wrap items-start justify-between gap-2"><h4 className="font-medium">{paper.title}</h4><Badge variant="outline">{paper.source_kind.toUpperCase()}</Badge></div>
    <p className="text-muted-foreground mt-1 text-xs">{paper.authors.join("; ")} · {paper.publication_year ?? "年份未知"} · {paper.bibtex_type}</p>
    <p className="text-muted-foreground text-xs">{paper.doi ? `DOI: ${paper.doi}` : "无 DOI"} · {paper.relative_source_path}</p>
    {paper.evidence.map((item) => <blockquote key={`${item.page_number}-${item.chunk_index}`} className="mt-2 border-l-2 pl-2 text-xs">p.{item.page_number}: {item.text}</blockquote>)}
  </article>;
}

export function LocalPaperLibraryWorkbench() {
  const user = useAuthStore((state) => state.user);
  const accessToken = useAuthStore((state) => state.accessToken);
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [searchLimit, setSearchLimit] = useState(10);
  const [question, setQuestion] = useState("");
  const [mindmapQuery, setMindmapQuery] = useState("");
  const [mindmapQuestion, setMindmapQuestion] = useState("");
  const [mindmapLimit, setMindmapLimit] = useState(10);
  const [mindmapFormat, setMindmapFormat] = useState<"markdown" | "opml">("markdown");
  const [mindmapResult, setMindmapResult] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const status = useQuery({ queryKey: ["local-paper-library", "status"], queryFn: literatureResearchApi.localLibraryStatus, retry: false, refetchInterval: (result) => result.state.data?.latest_sync?.status === "RUNNING" || result.state.data?.latest_sync?.status === "QUEUED" ? 3000 : false });
  const syncRunId = status.data?.latest_sync?.id;
  const syncSequence = Number(status.data?.latest_sync?.summary_json?.sequence ?? 0);
  const isSyncActive = status.data?.latest_sync?.status === "RUNNING" || status.data?.latest_sync?.status === "QUEUED";
  const syncProtocols = useMemo(
    () => (accessToken ? [`access_token.${accessToken}`, "research"] : undefined),
    [accessToken],
  );
  const onSyncEvent = useCallback((message: MessageEvent) => {
    const envelope = JSON.parse(String(message.data)) as { type?: string; data?: { sync_run_id?: string; status?: string; summary_json?: Record<string, unknown>; error_message?: string | null; updated_at?: string | null } };
    if (envelope.type !== "local_paper_sync_event" || envelope.data?.sync_run_id !== syncRunId) return;
    queryClient.setQueryData<LocalLibraryStatus>(["local-paper-library", "status"], (previous) => previous ? {
      ...previous,
      status: envelope.data?.status ?? previous.status,
      last_sync_summary: envelope.data?.summary_json ?? previous.last_sync_summary,
      latest_sync: previous.latest_sync ? {
        ...previous.latest_sync,
        status: envelope.data?.status ?? previous.latest_sync.status,
        summary_json: envelope.data?.summary_json ?? previous.latest_sync.summary_json,
        error_message: envelope.data?.error_message,
        updated_at: envelope.data?.updated_at,
      } : previous.latest_sync,
    } : previous);
    if (["COMPLETED", "FAILED", "CANCELLED"].includes(envelope.data?.status ?? "")) {
      // A terminal stream event only carries the sync snapshot.  Refetch the
      // authoritative status counters so the header cannot retain old totals.
      void queryClient.invalidateQueries({ queryKey: ["local-paper-library", "status"] });
    }
  }, [queryClient, syncRunId]);
  const { connect: connectSyncStream, disconnect: disconnectSyncStream } = useWebSocket({
    url: `${WS_URL}/api/v1/research/local-library/sync/${syncRunId ?? "inactive"}/stream?after_sequence=${syncSequence}`,
    protocols: syncProtocols,
    onMessage: onSyncEvent,
  });
  useEffect(() => {
    if (!isSyncActive || !syncRunId) return;
    connectSyncStream();
    return disconnectSyncStream;
  }, [connectSyncStream, disconnectSyncStream, isSyncActive, syncRunId, syncSequence]);
  const sync = useMutation({ mutationFn: literatureResearchApi.syncLocalLibrary, onSuccess: () => { setMessage("同步任务已提交，正在由本地 CPU worker 处理。"); void queryClient.invalidateQueries({ queryKey: ["local-paper-library", "status"] }); }, onError: (error) => setMessage(getErrorMessage(error, "无法提交同步任务")) });
  const search = useMutation({ mutationFn: (params: { value: string; limit: number }) => literatureResearchApi.searchLocalLibrary({ query: params.value, limit: params.limit }), onError: (error) => setMessage(getErrorMessage(error, "本地检索失败")) });
  const ask = useMutation({ mutationFn: (params: { question: string; limit: number; paper_ids: string[]; query_context?: string }) => literatureResearchApi.askLocalLibrary(params), onError: (error) => setMessage(getErrorMessage(error, "本地问答失败")) });
  const mindmap = useMutation({
    mutationFn: (params: { query: string; question: string; limit: number; output_format: "markdown" | "opml" }) =>
      literatureResearchApi.analyzePapersMindmap(params).then((blob) => blob.text()),
    onSuccess: (data) => setMindmapResult(data),
    onError: (error) => setMessage(getErrorMessage(error, "思维导图生成失败")),
  });
  const runSearch = (event: FormEvent) => { event.preventDefault(); setMessage(null); search.mutate({ value: query, limit: searchLimit }); };
  const runAsk = (event: FormEvent) => {
    event.preventDefault();
    setMessage(null);
    if (!search.data?.items.length) {
      setMessage("请先完成本地检索；问答会严格限定在当前检索出的论文范围内。");
      return;
    }
    ask.mutate({
      question,
      limit: Math.min(16, searchLimit, search.data.items.length),
      paper_ids: search.data.items.map((paper) => paper.id),
      query_context: query.trim() || undefined,
    });
  };
  const runMindmap = (event: FormEvent) => { event.preventDefault(); setMessage(null); setMindmapResult(null); mindmap.mutate({ query: mindmapQuery, question: mindmapQuestion || mindmapQuery, limit: mindmapLimit, output_format: mindmapFormat }); };
  const library = status.data;
  const canUseAdminControls = user?.is_app_admin === true || user?.role === "admin";
  return <Card className="border-primary/30">
    <CardHeader><CardTitle className="flex items-center gap-2"><Database className="size-5" />本地论文库（Zotero）</CardTitle><p className="text-muted-foreground text-sm">只检索只读挂载的 PDF 与静态 HTML；不调用 Crossref、OpenAlex、Unpaywall 或外部全文下载。</p></CardHeader>
    <CardContent className="space-y-4">
      {!canUseAdminControls && user !== null && <Alert variant="destructive"><AlertCircle /><AlertTitle>需要应用管理员权限</AlertTitle><AlertDescription>当前登录账号不是应用管理员，不能读取或同步这个私有 Zotero 文库。</AlertDescription></Alert>}
      {status.isError && <Alert variant="destructive"><AlertCircle /><AlertTitle>本地论文库状态读取失败</AlertTitle><AlertDescription>{getErrorMessage(status.error, "无法连接本地论文库接口。请检查后端服务与登录状态。")}</AlertDescription></Alert>}
      {message && <Alert variant="warning"><AlertCircle /><AlertTitle>本地库提示</AlertTitle><AlertDescription>{message}</AlertDescription></Alert>}
      <div className="flex flex-wrap items-center gap-3 rounded-lg bg-muted/50 p-3 text-sm"><Badge>{library?.status ?? (status.isError ? "状态不可用" : "读取状态中")}</Badge><span>已登记元数据 {library?.catalogued_papers ?? library?.indexed_papers ?? 0} 篇</span><span>当前可检索 {library?.searchable_papers ?? library?.current_indexed_papers ?? 0} 篇</span><span>待重建 {library?.stale_indexed_papers ?? 0} 篇</span><span>源文件缺失 {library?.missing_source_papers ?? library?.missing_papers ?? 0} 篇</span><span>本次待核验 {library?.latest_quarantine_items ?? 0} 项</span><Button size="sm" variant="outline" onClick={() => sync.mutate()} disabled={!canUseAdminControls || sync.isPending || library?.latest_sync?.status === "RUNNING"}><RefreshCw className="mr-1 size-4" />{sync.isPending ? "提交中" : "手动同步/增量重建"}</Button></div>
      {library?.latest_sync && <p className="text-muted-foreground text-xs">最近任务：{library.latest_sync.status} · BibTeX 条目 {Number(library.latest_sync.summary_json.processed ?? 0)}/{Number(library.latest_sync.summary_json.total_bibtex_entries ?? library.latest_sync.summary_json.total_bibtex ?? 0)} · 重建 {Number(library.latest_sync.summary_json.indexed ?? 0)} · 未变更 {Number(library.latest_sync.summary_json.unchanged ?? 0)} · 去重条目 {Number(library.latest_sync.summary_json.duplicate ?? 0)} · 未匹配 BibTeX {Number(library.latest_sync.summary_json.unmatched_bibtex ?? 0)} · 已引用源文件 {Number(library.latest_sync.summary_json.referenced_source_files ?? 0)}/{Number(library.latest_sync.summary_json.total_supported_sources ?? library.latest_sync.summary_json.total_sources ?? 0)} · 关联附件 {Number(library.latest_sync.summary_json.related_attachments ?? 0)} · 真正未归属文件 {Number(library.latest_sync.summary_json.unmatched_source ?? 0)} · 错误 {Number(library.latest_sync.summary_json.errors ?? 0)}{library.latest_sync.summary_json.current_citekey ? ` · 当前：${String(library.latest_sync.summary_json.current_citekey)}` : ""}{library.latest_sync.error_message ? ` · ${library.latest_sync.error_message}` : ""}</p>}
      <form onSubmit={runSearch} className="space-y-2">
        <div className="flex gap-2">
          <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="主题、关键词、作者、DOI、年份或 BibTeX 类型筛选后检索" />
          <Button disabled={search.isPending}><Search className="mr-1 size-4" />检索</Button>
        </div>
        <div className="flex items-center gap-2">
          <Label htmlFor="search-limit" className="text-xs whitespace-nowrap">返回数量（1-50）</Label>
          <Input id="search-limit" type="number" min={1} max={50} value={searchLimit} onChange={(e) => setSearchLimit(Math.min(50, Math.max(1, Number(e.target.value))))} className="w-20 text-xs" />
        </div>
      </form>
      {search.data && <div className="space-y-2"><p className="text-sm">命中 {search.data.total} 篇（{search.data.retrieval_mode === "hybrid" ? `元数据预过滤 · 块级 BM25 + 向量 RRF · BGE 重排；候选 ${search.data.candidate_chunks} 块/${search.data.candidate_papers} 篇，低分拒绝 ${search.data.rejected_by_score} 块` : "元数据排序"}，展示 {search.data.items.length} 篇）</p>{search.data.insufficient_evidence && <Alert variant="warning"><AlertCircle /><AlertTitle>证据不足</AlertTitle><AlertDescription>候选块未通过 BGE 相关性阈值，系统没有用 MMR 补入低相关论文。</AlertDescription></Alert>}{search.data.items.map((paper) => <PaperRow key={paper.id} paper={paper} />)}<div className="flex flex-wrap gap-2">{(["markdown", "csv", "bibtex", "opml"] as const).map((format) => <Button key={format} size="sm" variant="outline" type="button" onClick={() => void download(format, query).catch((error) => setMessage(getErrorMessage(error)))}><FileText className="mr-1 size-3" />{format.toUpperCase()}</Button>)}</div></div>}
      <form onSubmit={runAsk} className="space-y-2">
        <Label htmlFor="local-library-question">基于当前检索结果的全文问答（仅使用页码证据）</Label>
        <Textarea id="local-library-question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：这些论文如何处理语义通信中的码率—失真权衡？" />
        <Button disabled={ask.isPending || question.trim().length < 3 || !search.data?.items.length}>{ask.isPending ? "检索并回答中…" : "有证据问答"}</Button>
      </form>
      {ask.data && <div className="rounded-lg border p-4 space-y-3 text-sm"><div className="prose prose-sm max-w-none"><MarkdownContent content={ask.data.answer} /></div><div className="mt-3 space-y-2">{ask.data.citations.map((citation) => <blockquote key={`${citation.paper_id}-${citation.page_number}`} className="border-l-2 pl-2 text-xs"><strong>{citation.title}</strong> · {citation.authors.join("; ") || "作者未提供"} · {citation.publication_year ?? "年份未提供"} {citation.doi ? `· ${citation.doi}` : ""} · p.{citation.page_number}: {citation.text}</blockquote>)}</div></div>}
      <div className="rounded-lg border p-3 space-y-3">
        <h3 className="text-sm font-medium">论文群深度分析 / 思维导图</h3>
        <form onSubmit={runMindmap} className="space-y-2">
          <Input value={mindmapQuery} onChange={(e) => setMindmapQuery(e.target.value)} placeholder="检索关键词 / 研究课题" required />
          <Input value={mindmapQuestion} onChange={(e) => setMindmapQuestion(e.target.value)} placeholder="分析问题（留空则与检索词相同）" />
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-2">
              <Label htmlFor="mindmap-limit" className="text-xs whitespace-nowrap">论文数（1-50）</Label>
              <Input id="mindmap-limit" type="number" min={1} max={50} value={mindmapLimit} onChange={(e) => setMindmapLimit(Math.min(50, Math.max(1, Number(e.target.value))))} className="w-20 text-xs" />
            </div>
            <div className="flex items-center gap-2">
              <Label htmlFor="mindmap-format" className="text-xs whitespace-nowrap">输出格式</Label>
              <select id="mindmap-format" value={mindmapFormat} onChange={(e) => setMindmapFormat(e.target.value as "markdown" | "opml")} className="border rounded px-2 py-1 text-xs">
                <option value="markdown">Markdown</option>
                <option value="opml">OPML</option>
              </select>
            </div>
            <Button type="submit" disabled={mindmap.isPending || mindmapQuery.trim().length < 1}>{mindmap.isPending ? "分析中…" : "生成思维导图"}</Button>
          </div>
        </form>
        {mindmapResult && (
          <div className="space-y-2">
            <div className="flex gap-2">
              <Button size="sm" variant="outline" type="button" onClick={() => { const blob = new Blob([mindmapResult], { type: "text/plain" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `paper-mindmap.${mindmapFormat === "opml" ? "opml" : "md"}`; a.click(); URL.revokeObjectURL(url); }}><FileText className="mr-1 size-3" />下载</Button>
            </div>
            <pre className="rounded bg-muted p-3 text-xs overflow-auto max-h-96 whitespace-pre-wrap">{mindmapResult}</pre>
          </div>
        )}
      </div>
      {library?.quarantine.length ? <details className="text-xs"><summary>查看最近待核验/跳过项</summary><ul className="mt-2 list-disc space-y-1 pl-5">{library.quarantine.slice(0, 20).map((item, index) => <li key={`${item.item_kind}-${index}`}>{item.item_kind}: {item.relative_path ?? item.citekey ?? "—"} · {item.detail}</li>)}</ul></details> : null}
    </CardContent>
  </Card>;
}
