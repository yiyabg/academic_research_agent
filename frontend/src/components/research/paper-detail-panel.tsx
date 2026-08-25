import { AlertTriangle, RotateCcw, ThumbsDown, ThumbsUp, X } from "lucide-react";

import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/components/ui";
import type { PaperDetail } from "@/types/literature-research";

export function PaperDetailPanel({
  detail,
  loading,
  onClose,
  onReanalyze,
  onRelevanceFeedback,
  feedbackBusy,
  feedbackStatus,
}: {
  detail?: PaperDetail;
  loading: boolean;
  onClose: () => void;
  onReanalyze: () => void;
  onRelevanceFeedback: (decision: "INCLUDE" | "EXCLUDE") => void;
  feedbackBusy: boolean;
  feedbackStatus?: string | null;
}) {
  return (
    <Card className="sticky top-4 max-h-[calc(100vh-7rem)] overflow-auto">
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div>
          <CardTitle className="leading-snug">
            {loading ? "加载论文…" : detail?.candidate.title}
          </CardTitle>
          {detail?.analysis_attempt && (
            <p className="text-muted-foreground mt-2 text-xs">
              分析 attempt {detail.analysis_attempt}
            </p>
          )}
        </div>
        <Button variant="ghost" size="icon" onClick={onClose} aria-label="关闭论文详情">
          <X className="h-4 w-4" />
        </Button>
      </CardHeader>
      {detail && (
        <CardContent className="space-y-5">
          <div className="flex flex-wrap gap-2">
            <Badge variant={detail.candidate.hard_eligible ? "default" : "destructive"}>
              Hard {detail.candidate.hard_eligible ? "PASS" : "FAIL/UNKNOWN"}
            </Badge>
            <Badge variant="outline">{detail.candidate.relevance_decision ?? "PENDING"}</Badge>
            <Badge variant="secondary">{detail.versions.length} version(s)</Badge>
          </div>

          <section>
            <h3 className="mb-2 text-sm font-semibold">约束账本</h3>
            <div className="space-y-2">
              {detail.candidate.constraints.map((item) => (
                <div key={item.constraint_id} className="rounded-lg border p-3 text-xs">
                  <div className="flex justify-between gap-2">
                    <span className="font-mono">{item.constraint_id}</span>
                    <Badge
                      variant={
                        item.decision === "PASS"
                          ? "default"
                          : item.decision === "FAIL"
                            ? "destructive"
                            : "outline"
                      }
                    >
                      {item.decision}
                    </Badge>
                  </div>
                  <p className="text-muted-foreground mt-1">{item.reason_code}</p>
                </div>
              ))}
            </div>
          </section>

          {detail.candidate.relevance_facet_judgement && (
            <section>
              <div className="mb-2 flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">证据化 Facet 判定</h3>
                <Badge
                  variant={
                    detail.candidate.relevance_facet_judgement.centrality === "CENTRAL"
                      ? "default"
                      : "outline"
                  }
                >
                  {detail.candidate.relevance_facet_judgement.centrality} ·{" "}
                  {detail.candidate.relevance_facet_judgement.score.toFixed(3)}
                </Badge>
              </div>
              <div className="space-y-2">
                {detail.candidate.relevance_facet_judgement.facets.map((facet) => (
                  <div key={facet.facet_id} className="rounded-lg border p-3 text-xs">
                    <div className="flex justify-between gap-2">
                      <span className="font-mono">{facet.facet_id}</span>
                      <Badge
                        variant={
                          facet.status === "SUPPORTED"
                            ? "default"
                            : facet.status === "NOT_SUPPORTED"
                              ? "destructive"
                              : "outline"
                        }
                      >
                        {facet.status}
                      </Badge>
                    </div>
                    <p className="mt-1">{facet.rationale}</p>
                    <p className="text-muted-foreground mt-1 font-mono">
                      {facet.evidence_ids.join(", ") || "no cited metadata evidence"}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {detail.analysis && (
            <section>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold">结构化分析</h3>
                <span className="text-xs tabular-nums">
                  覆盖率 {(detail.analysis.audit.evidence_coverage * 100).toFixed(0)}%
                </span>
              </div>
              {(detail.analysis.audit.contradicted_count > 0 ||
                detail.analysis.audit.unsupported_count > 0) && (
                <div className="text-destructive mb-3 flex gap-2 text-xs">
                  <AlertTriangle className="h-4 w-4" />
                  {detail.analysis.audit.contradicted_count} contradicted /{" "}
                  {detail.analysis.audit.unsupported_count} unsupported
                </div>
              )}
              <div className="space-y-3">
                {detail.analysis.sections.map((section) => (
                  <div key={section.section_id} className="rounded-lg border p-3">
                    <div className="mb-1 flex justify-between text-xs font-semibold uppercase">
                      <span>{section.section_id}</span>
                      <span>{section.status}</span>
                    </div>
                    <p className="text-sm leading-relaxed">{section.summary || "未报告"}</p>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section>
            <h3 className="mb-2 text-sm font-semibold">证据与图表</h3>
            <p className="text-muted-foreground mb-2 text-xs">
              {detail.evidence.length} evidence spans / {detail.figures.length} figure or table
              captions
            </p>
            <div className="space-y-2">
              {detail.evidence.slice(0, 12).map((item) => (
                <blockquote
                  key={item.evidence_id}
                  className="border-brand/50 border-l-2 pl-3 text-xs leading-relaxed"
                >
                  <p>{item.quote}</p>
                  <footer className="text-muted-foreground mt-1 font-mono">
                    {item.evidence_id} · page {item.page_number ?? "n/a"} ·{" "}
                    {item.block_text_sha256.slice(0, 12)}…
                  </footer>
                </blockquote>
              ))}
            </div>
          </section>

          <section className="rounded-lg border p-3">
            <h3 className="text-sm font-semibold">人工相关性反馈</h3>
            <p className="text-muted-foreground mt-1 text-xs">
              反馈会保存为项目纠错记忆并进入隔离的向量索引，供本项目后续协议草案召回；不会修改当前已批准协议。
            </p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <Button
                variant="outline"
                disabled={feedbackBusy}
                onClick={() => onRelevanceFeedback("INCLUDE")}
              >
                <ThumbsUp className="h-4 w-4" />
                标记核心相关
              </Button>
              <Button
                variant="outline"
                disabled={feedbackBusy}
                onClick={() => onRelevanceFeedback("EXCLUDE")}
              >
                <ThumbsDown className="h-4 w-4" />
                标记排除
              </Button>
            </div>
            {feedbackStatus && (
              <p className="text-muted-foreground mt-2 text-xs">{feedbackStatus}</p>
            )}
          </section>

          {detail.analysis && (
            <Button variant="outline" className="w-full" onClick={onReanalyze}>
              <RotateCcw className="h-4 w-4" />
              仅重分析此论文
            </Button>
          )}
        </CardContent>
      )}
    </Card>
  );
}
