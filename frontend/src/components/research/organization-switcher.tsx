"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Settings2, Trash2, Users } from "lucide-react";

import {
  Badge,
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui";
import { literatureResearchApi } from "@/lib/literature-research-api";
import { qk } from "@/lib/query-keys";
import { useResearchOrganizationStore } from "@/stores";

function slugify(value: string) {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63);
}

export function ResearchOrganizationSwitcher() {
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [memberEmail, setMemberEmail] = useState("");
  const activeOrganizationId = useResearchOrganizationStore(
    (state) => state.activeOrganizationId,
  );
  const setActiveOrganizationId = useResearchOrganizationStore(
    (state) => state.setActiveOrganizationId,
  );
  const organizations = useQuery({
    queryKey: qk.literatureResearch.organizations(),
    queryFn: literatureResearchApi.listOrganizations,
  });
  const activeOrganization = organizations.data?.find(
    (organization) => organization.id === activeOrganizationId,
  );
  useEffect(() => {
    if (
      activeOrganizationId &&
      organizations.isSuccess &&
      !organizations.data.some((organization) => organization.id === activeOrganizationId)
    ) {
      setActiveOrganizationId(null);
    }
  }, [
    activeOrganizationId,
    organizations.data,
    organizations.isSuccess,
    setActiveOrganizationId,
  ]);
  const members = useQuery({
    queryKey: qk.literatureResearch.organizationMembers(activeOrganizationId),
    queryFn: () => literatureResearchApi.organizationMembers(activeOrganizationId!),
    enabled: Boolean(activeOrganizationId && dialogOpen),
  });

  const createOrganization = useMutation({
    mutationFn: () => literatureResearchApi.createOrganization({ name, slug }),
    onSuccess: async (organization) => {
      setActiveOrganizationId(organization.id);
      setName("");
      setSlug("");
      await queryClient.invalidateQueries({
        queryKey: qk.literatureResearch.organizations(),
      });
    },
  });
  const addMember = useMutation({
    mutationFn: () =>
      literatureResearchApi.addOrganizationMember(activeOrganizationId!, memberEmail),
    onSuccess: async () => {
      setMemberEmail("");
      await queryClient.invalidateQueries({
        queryKey: qk.literatureResearch.organizationMembers(activeOrganizationId),
      });
    },
  });
  const removeMember = useMutation({
    mutationFn: (userId: string) =>
      literatureResearchApi.removeOrganizationMember(activeOrganizationId!, userId),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: qk.literatureResearch.organizationMembers(activeOrganizationId),
      }),
  });

  return (
    <div className="flex items-center gap-2">
      <Select
        value={activeOrganizationId ?? "personal"}
        onValueChange={(value) =>
          setActiveOrganizationId(value === "personal" ? null : value)
        }
      >
        <SelectTrigger className="w-[220px]" aria-label="活动研究组织">
          <SelectValue placeholder="选择研究空间" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="personal">个人研究空间</SelectItem>
          {organizations.data?.map((organization) => (
            <SelectItem key={organization.id} value={organization.id}>
              {organization.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button type="button" variant="outline" size="icon" onClick={() => setDialogOpen(true)}>
        <Settings2 className="h-4 w-4" />
        <span className="sr-only">管理研究组织</span>
      </Button>
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>研究组织与成员</DialogTitle>
            <DialogDescription>
              组织项目只对当前成员可见；只有组织 OWNER 可以增删成员。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-5">
            <div className="space-y-3 rounded-lg border p-4">
              <div className="font-medium">新建独立研究组织</div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label htmlFor="organization-name">名称</Label>
                  <Input
                    id="organization-name"
                    value={name}
                    onChange={(event) => {
                      setName(event.target.value);
                      setSlug(slugify(event.target.value));
                    }}
                  />
                </div>
                <div>
                  <Label htmlFor="organization-slug">Slug</Label>
                  <Input
                    id="organization-slug"
                    value={slug}
                    onChange={(event) => setSlug(slugify(event.target.value))}
                  />
                </div>
              </div>
              <Button
                type="button"
                disabled={name.trim().length < 2 || slug.length < 3 || createOrganization.isPending}
                onClick={() => createOrganization.mutate()}
              >
                创建组织
              </Button>
            </div>
            {activeOrganization && (
              <div className="space-y-3 rounded-lg border p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 font-medium">
                    <Users className="h-4 w-4" />{activeOrganization.name}
                  </div>
                  <Badge variant="outline">{activeOrganization.current_user_role}</Badge>
                </div>
                {activeOrganization.current_user_role === "OWNER" && (
                  <div className="flex gap-2">
                    <Input
                      type="email"
                      placeholder="已注册用户邮箱"
                      value={memberEmail}
                      onChange={(event) => setMemberEmail(event.target.value)}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      disabled={!memberEmail || addMember.isPending}
                      onClick={() => addMember.mutate()}
                    >
                      添加成员
                    </Button>
                  </div>
                )}
                <div className="divide-y rounded-md border">
                  {members.data?.map((member) => (
                    <div key={member.user_id} className="flex items-center gap-3 px-3 py-2 text-sm">
                      <div className="min-w-0 flex-1">
                        <div className="truncate">{member.full_name || member.email}</div>
                        {member.full_name && (
                          <div className="text-muted-foreground truncate text-xs">{member.email}</div>
                        )}
                      </div>
                      <Badge variant="outline">{member.role}</Badge>
                      {activeOrganization.current_user_role === "OWNER" && member.role !== "OWNER" && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          onClick={() => removeMember.mutate(member.user_id)}
                        >
                          <Trash2 className="h-4 w-4" />
                          <span className="sr-only">移除成员</span>
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
