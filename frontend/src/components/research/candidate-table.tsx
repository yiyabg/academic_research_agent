import { Badge, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { Candidate } from "@/types/literature-research";

function decisionVariant(decision?: string) {
  if (decision === "FAIL") return "destructive" as const;
  if (decision === "PASS") return "default" as const;
  return "outline" as const;
}

export function CandidateTable({
  candidates,
  selectedWorkId,
  onSelect,
}: {
  candidates: Candidate[];
  selectedWorkId: string | null;
  onSelect: (workId: string) => void;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>论文</TableHead>
          <TableHead>日期 / Venue</TableHead>
          <TableHead>硬约束</TableHead>
          <TableHead>相关性</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {candidates.map((paper) => (
          <TableRow
            key={paper.work_id}
            tabIndex={0}
            role="button"
            aria-label={`查看 ${paper.title}`}
            className={cn("cursor-pointer", selectedWorkId === paper.work_id && "bg-muted")}
            onClick={() => onSelect(paper.work_id)}
            onKeyDown={(event) => event.key === "Enter" && onSelect(paper.work_id)}
          >
            <TableCell className="min-w-72">
              <p className="font-medium">{paper.title}</p>
              <p className="text-muted-foreground mt-1 line-clamp-1 text-xs">
                {paper.authors.join(", ") || "作者未报告"}
              </p>
            </TableCell>
            <TableCell className="text-xs">
              <p>{paper.effective_publication_date ?? "未知日期"}</p>
              <p className="text-muted-foreground mt-1">{paper.venue ?? paper.document_type}</p>
            </TableCell>
            <TableCell>
              <Badge variant={paper.hard_eligible ? "default" : paper.hard_eligible === false ? "destructive" : "outline"}>
                {paper.hard_eligible ? "PASS" : paper.hard_eligible === false ? "FAIL" : "UNKNOWN"}
              </Badge>
              {(paper.hard_fail_count > 0 || paper.hard_unknown_count > 0) && (
                <p className="text-muted-foreground mt-1 text-[11px]">
                  {paper.hard_fail_count} fail / {paper.hard_unknown_count} unknown
                </p>
              )}
            </TableCell>
            <TableCell>
              <Badge variant={decisionVariant(paper.relevance_decision)}>
                {paper.relevance_decision ?? "PENDING"}
              </Badge>
              {paper.relevance_score !== undefined && (
                <p className="mt-1 text-xs tabular-nums">{paper.relevance_score.toFixed(3)}</p>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
