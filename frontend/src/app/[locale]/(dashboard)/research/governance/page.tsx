"use client";

import { type FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BrainCircuit, Plus, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

import { PageHeader } from "@/components/dashboard/page-header";
import { ResearchOrganizationSwitcher } from "@/components/research";
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from "@/components/ui";
import { ROUTES } from "@/lib/constants";
import { literatureResearchApi } from "@/lib/literature-research-api";
import { createClientId } from "@/lib/client-id";
import { qk } from "@/lib/query-keys";
import { formatDateTime, getErrorMessage } from "@/lib/utils";
import { useResearchOrganizationStore } from "@/stores";
import type { ResearchMemoryType } from "@/types/literature-research";

const MEMORY_TYPES: ResearchMemoryType[] = [
  "QUERY_TERM",
  "EXCLUSION_DECISION",
  "CORRECTION",
  "DISPLAY_PREFERENCE",
  "ARTIFACT_NOTE",
];

function parseObject(text: string, label: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label}必须是 JSON 对象`);
  }
  return value as Record<string, unknown>;
}

export default function ResearchGovernancePage() {
  const queryClient = useQueryClient();
  const activeOrganizationId = useResearchOrganizationStore((state) => state.activeOrganizationId);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [profileJson, setProfileJson] = useState("{}");
  const [profileNote, setProfileNote] = useState("");
  const [profileInitialized, setProfileInitialized] = useState(false);
  const [memoryType, setMemoryType] = useState<ResearchMemoryType>("CORRECTION");
  const [memoryJson, setMemoryJson] = useState('{\n  "note": ""\n}');

  const projects = useQuery({
    queryKey: qk.literatureResearch.projects(activeOrganizationId),
    queryFn: () => literatureResearchApi.listProjects(activeOrganizationId),
  });
  const profile = useQuery({
    queryKey: qk.literatureResearch.profile(),
    queryFn: literatureResearchApi.profile,
  });
  const visibleProjects = activeOrganizationId
    ? projects.data
    : projects.data?.filter((project) => project.organization_id === null);

  useEffect(() => {
    if (!selectedProjectId && visibleProjects?.[0]) {
      setSelectedProjectId(visibleProjects[0].id);
    }
  }, [selectedProjectId, visibleProjects]);

  useEffect(() => {
    if (!profileInitialized && profile.data !== undefined) {
      setProfileJson(JSON.stringify(profile.data?.preferences ?? {}, null, 2));
      setProfileNote(profile.data?.confirmation_note ?? "");
      setProfileInitialized(true);
    }
  }, [profile.data, profileInitialized]);

  const memories = useQuery({
    queryKey: qk.literatureResearch.memories(selectedProjectId),
    queryFn: () => literatureResearchApi.projectMemories(selectedProjectId),
    enabled: Boolean(selectedProjectId),
  });

  const confirmProfile = useMutation({
    mutationFn: literatureResearchApi.confirmProfile,
    onSuccess: async (saved) => {
      toast.success(`研究画像 v${saved.version} 已由你确认`);
      await queryClient.invalidateQueries({ queryKey: qk.literatureResearch.profile() });
    },
    onError: (error) => toast.error(getErrorMessage(error, "画像保存失败")),
  });

  const addMemory = useMutation({
    mutationFn: (content: Record<string, unknown>) =>
      literatureResearchApi.createProjectMemory(selectedProjectId, {
        memory_type: memoryType,
        content,
        source: "USER_FEEDBACK",
        source_id: `manual:${createClientId("memory")}`,
        confidence: 1,
        valid_from: new Date().toISOString(),
      }),
    onSuccess: async () => {
      toast.success("项目记忆已保存并进入异步索引队列");
      setMemoryJson('{\n  "note": ""\n}');
      await queryClient.invalidateQueries({
        queryKey: qk.literatureResearch.memories(selectedProjectId),
      });
    },
    onError: (error) => toast.error(getErrorMessage(error, "项目记忆保存失败")),
  });

  const submitProfile = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      confirmProfile.mutate({
        preferences: parseObject(profileJson, "研究画像"),
        confirmation_note: profileNote,
      });
    } catch (error) {
      toast.error(getErrorMessage(error, "研究画像 JSON 无效"));
    }
  };

  const submitMemory = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedProjectId) return;
    try {
      addMemory.mutate(parseObject(memoryJson, "项目记忆"));
    } catch (error) {
      toast.error(getErrorMessage(error, "项目记忆 JSON 无效"));
    }
  };

  return (
    <div className="space-y-6 pb-10">
      <PageHeader
        eyebrow="Governed memory"
        title="研究记忆与用户画像"
        description="只保存由用户明确确认的偏好与纠错；记忆不能批准协议，也不能覆盖硬约束或预算。"
        actions={
          <div className="flex flex-wrap gap-2">
            <ResearchOrganizationSwitcher />
            <Button asChild variant="outline">
              <Link href={ROUTES.RESEARCH}>
                <ArrowLeft className="h-4 w-4" />
                返回研究项目
              </Link>
            </Button>
          </div>
        }
      />

      <Alert>
        <ShieldCheck className="h-4 w-4" />
        <AlertTitle>受保护的记忆边界</AlertTitle>
        <AlertDescription>
          API 会递归拒绝凭据字段以及 constraints、time_scope、quantity_policy、quality_floor、
          approved_protocol_hash。保存前不要填写 API key、token、cookie 或密码。
        </AlertDescription>
      </Alert>

      <div className="grid gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-start justify-between gap-3">
              <div>
                <CardTitle>用户确认画像</CardTitle>
                <CardDescription>
                  只在你提交后生成不可变新版本，供未来 AI 协议建议参考。
                </CardDescription>
              </div>
              {profile.data && <Badge variant="outline">v{profile.data.version}</Badge>}
            </div>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={submitProfile}>
              <div className="space-y-2">
                <Label htmlFor="profile-preferences">偏好 JSON</Label>
                <Textarea
                  id="profile-preferences"
                  value={profileJson}
                  onChange={(event) => setProfileJson(event.target.value)}
                  className="min-h-48 font-mono text-xs"
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="profile-note">确认说明</Label>
                <Input
                  id="profile-note"
                  value={profileNote}
                  onChange={(event) => setProfileNote(event.target.value)}
                  minLength={3}
                  placeholder="例如：本人确认这些偏好用于后续协议建议"
                  required
                />
              </div>
              <div className="flex items-center justify-between gap-3">
                <p className="text-muted-foreground text-xs">
                  {profile.data
                    ? `上次确认：${formatDateTime(profile.data.confirmed_at)}`
                    : "尚无已确认画像"}
                </p>
                <Button type="submit" disabled={confirmProfile.isPending}>
                  <BrainCircuit className="h-4 w-4" />
                  {confirmProfile.isPending ? "保存中…" : "确认并创建新版本"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>新增项目记忆</CardTitle>
            <CardDescription>
              保存查询词、纳排纠错、展示偏好或工件备注，并异步写入项目隔离的 Qdrant。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={submitMemory}>
              <div className="space-y-2">
                <Label>项目</Label>
                <Select value={selectedProjectId} onValueChange={setSelectedProjectId}>
                  <SelectTrigger>
                    <SelectValue placeholder="选择项目" />
                  </SelectTrigger>
                  <SelectContent>
                    {visibleProjects?.map((project) => (
                      <SelectItem key={project.id} value={project.id}>
                        {project.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>记忆类型</Label>
                <Select
                  value={memoryType}
                  onValueChange={(value) => setMemoryType(value as ResearchMemoryType)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {MEMORY_TYPES.map((value) => (
                      <SelectItem key={value} value={value}>
                        {value}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="memory-content">内容 JSON</Label>
                <Textarea
                  id="memory-content"
                  value={memoryJson}
                  onChange={(event) => setMemoryJson(event.target.value)}
                  className="min-h-40 font-mono text-xs"
                  required
                />
              </div>
              <div className="flex justify-end">
                <Button type="submit" disabled={!selectedProjectId || addMemory.isPending}>
                  <Plus className="h-4 w-4" />
                  {addMemory.isPending ? "保存中…" : "保存项目记忆"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>当前有效的项目记忆</CardTitle>
          <CardDescription>
            列表以 PostgreSQL 为准；Qdrant 只用于未来草案的语义召回。
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!selectedProjectId ? (
            <p className="text-muted-foreground text-sm">当前研究空间没有项目。</p>
          ) : memories.isLoading ? (
            <p className="text-muted-foreground text-sm">正在加载项目记忆…</p>
          ) : memories.isError ? (
            <p className="text-destructive text-sm">{getErrorMessage(memories.error)}</p>
          ) : memories.data?.length ? (
            <div className="grid gap-3 lg:grid-cols-2">
              {memories.data.map((memory) => (
                <div key={memory.id} className="border-border rounded-lg border p-4">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <Badge variant="outline">{memory.memory_type}</Badge>
                    <span className="text-muted-foreground text-xs">
                      {formatDateTime(memory.created_at)}
                    </span>
                  </div>
                  <pre className="bg-muted/40 max-h-48 overflow-auto rounded p-3 text-xs whitespace-pre-wrap">
                    {JSON.stringify(memory.content, null, 2)}
                  </pre>
                  <p className="text-muted-foreground mt-2 text-xs">
                    {memory.source} · {memory.source_id} · confidence {memory.confidence}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-muted-foreground text-sm">该项目尚无有效记忆。</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
