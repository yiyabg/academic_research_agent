import { Download } from "lucide-react";

import { Badge, Button } from "@/components/ui";
import { artifactDownloadUrl } from "@/lib/literature-research-api";
import type { ResearchArtifact } from "@/types/literature-research";

export function ArtifactDownloads({
  runId,
  artifacts,
}: {
  runId: string;
  artifacts: ResearchArtifact[];
}) {
  if (!artifacts.length)
    return <p className="text-muted-foreground text-sm">产物将在发布门禁前生成。</p>;
  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-xs">不可变产物代次：G{artifacts[0]?.generation}</p>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {artifacts.map((artifact) => (
          <div key={artifact.id} className="flex items-center gap-3 rounded-lg border p-3">
            <Badge variant="outline">{artifact.format}</Badge>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{artifact.filename}</p>
              <p className="text-muted-foreground font-mono text-[10px]">
                sha256 {artifact.sha256.slice(0, 12)}…
              </p>
            </div>
            <Button asChild variant="ghost" size="icon">
              <a
                href={artifactDownloadUrl(runId, artifact.id)}
                aria-label={`下载 ${artifact.filename}`}
              >
                <Download className="h-4 w-4" />
              </a>
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}
