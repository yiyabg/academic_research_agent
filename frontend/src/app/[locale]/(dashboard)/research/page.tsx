"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { BookOpenCheck, BrainCircuit, Plus } from "lucide-react";

import { PageHeader } from "@/components/dashboard/page-header";
import { ResearchOrganizationSwitcher } from "@/components/research";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/components/ui";
import { literatureResearchApi } from "@/lib/literature-research-api";
import { qk } from "@/lib/query-keys";
import { ROUTES } from "@/lib/constants";
import { useResearchOrganizationStore } from "@/stores";

export default function ResearchProjectsPage() {
  const activeOrganizationId = useResearchOrganizationStore(
    (state) => state.activeOrganizationId,
  );
  const projects = useQuery({
    queryKey: qk.literatureResearch.projects(activeOrganizationId),
    queryFn: () => literatureResearchApi.listProjects(activeOrganizationId),
  });
  const runs = useQuery({
    queryKey: qk.literatureResearch.runs(activeOrganizationId),
    queryFn: () => literatureResearchApi.listRuns(activeOrganizationId),
    refetchInterval: 10_000,
  });
  const visibleProjects = activeOrganizationId
    ? projects.data
    : projects.data?.filter((project) => project.organization_id === null);
  const visibleRuns = activeOrganizationId
    ? runs.data
    : runs.data?.filter((run) => run.organization_id === null);

  return (
    <div className="space-y-6 pb-8">
      <PageHeader
        eyebrow="Evidence-first research"
        title="学术论文深度调研"
        description="协议先行、质量门槛不可自动放宽、每项分析均可回到论文证据。"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <ResearchOrganizationSwitcher />
            <Button asChild variant="outline">
              <Link href={ROUTES.RESEARCH_GOVERNANCE}>
                <BrainCircuit className="h-4 w-4" />记忆与画像
              </Link>
            </Button>
            <Button asChild><Link href={ROUTES.RESEARCH_NEW}><Plus className="h-4 w-4" />新建调研</Link></Button>
          </div>
        }
      />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {visibleProjects?.map((project) => {
          const projectRuns = visibleRuns?.filter((run) => run.project_id === project.id) ?? [];
          const latest = projectRuns[0];
          return (
            <Card key={project.id}>
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <BookOpenCheck className="text-brand h-5 w-5" />
                  <Badge variant="outline">{project.status}</Badge>
                </div>
                <CardTitle className="pt-3">{project.title}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-muted-foreground line-clamp-2 min-h-10 text-sm">{project.description || "未提供项目描述"}</p>
                <div className="mt-4 flex items-center justify-between text-xs">
                  <span>{projectRuns.length} run(s)</span>
                  {latest ? (
                    <Button asChild variant="outline" size="sm">
                      <Link href={ROUTES.RESEARCH_RUN(project.id, latest.id)}>查看 {latest.state}</Link>
                    </Button>
                  ) : (
                    <span className="text-muted-foreground">尚未运行</span>
                  )}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
      {!projects.isLoading && !visibleProjects?.length && (
        <div className="rounded-xl border border-dashed p-12 text-center">
          <p className="text-muted-foreground mb-4">还没有研究项目。</p>
          <Button asChild><Link href={ROUTES.RESEARCH_NEW}>创建第一个可审计调研</Link></Button>
        </div>
      )}
    </div>
  );
}
