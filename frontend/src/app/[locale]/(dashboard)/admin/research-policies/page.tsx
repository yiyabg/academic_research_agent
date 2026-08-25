"use client";

import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, ScrollText, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { LoadingState } from "@/components/states";
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
  Textarea,
} from "@/components/ui";
import { literatureResearchApi } from "@/lib/literature-research-api";
import { formatDateTime, getErrorMessage } from "@/lib/utils";

function localDateTimeNow(): string {
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60_000);
  return now.toISOString().slice(0, 16);
}

function parseContent(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("策略内容必须是 JSON 对象");
  }
  return value as Record<string, unknown>;
}

export default function ResearchPoliciesAdminPage() {
  const queryClient = useQueryClient();
  const [policyKey, setPolicyKey] = useState("research.default");
  const [content, setContent] = useState('{\n  "preferred_sources": ["crossref", "openalex"]\n}');
  const [validFrom, setValidFrom] = useState(localDateTimeNow);
  const [validTo, setValidTo] = useState("");

  const policies = useQuery({
    queryKey: ["admin", "research-policy-versions"],
    queryFn: literatureResearchApi.policyVersions,
  });
  const create = useMutation({
    mutationFn: literatureResearchApi.createPolicyVersion,
    onSuccess: async (saved) => {
      toast.success(`${saved.policy_key} v${saved.version} created`);
      await queryClient.invalidateQueries({ queryKey: ["admin", "research-policy-versions"] });
    },
    onError: (error) => toast.error(getErrorMessage(error, "Policy version creation failed")),
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      create.mutate({
        policy_key: policyKey,
        content: parseContent(content),
        valid_from: new Date(validFrom).toISOString(),
        ...(validTo ? { valid_to: new Date(validTo).toISOString() } : {}),
      });
    } catch (error) {
      toast.error(getErrorMessage(error, "Policy JSON or validity window is invalid"));
    }
  };

  return (
    <div className="space-y-6">
      <Alert>
        <ShieldCheck className="h-4 w-4" />
        <AlertTitle>Advisory strategy only</AlertTitle>
        <AlertDescription>
          L4 policies may suggest sources, query vocabulary, or display strategy. Recursive API
          validation rejects credentials and any attempt to set constraints, time_scope,
          quantity_policy, quality_floor, or approved_protocol_hash.
        </AlertDescription>
      </Alert>

      <Card>
        <CardHeader>
          <CardTitle>Create immutable policy version</CardTitle>
          <CardDescription>
            The newest active version for each key is used by future AI protocol advice and is
            recorded by version and content hash in advice provenance.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="grid gap-4 lg:grid-cols-2" onSubmit={submit}>
            <div className="space-y-2 lg:col-span-2">
              <Label htmlFor="policy-key">Policy key</Label>
              <Input
                id="policy-key"
                value={policyKey}
                onChange={(event) => setPolicyKey(event.target.value)}
                pattern="[a-z][a-z0-9_.-]{2,99}"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="policy-from">Valid from</Label>
              <Input
                id="policy-from"
                type="datetime-local"
                value={validFrom}
                onChange={(event) => setValidFrom(event.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="policy-to">Valid to (optional)</Label>
              <Input
                id="policy-to"
                type="datetime-local"
                value={validTo}
                onChange={(event) => setValidTo(event.target.value)}
              />
            </div>
            <div className="space-y-2 lg:col-span-2">
              <Label htmlFor="policy-content">Strategy JSON</Label>
              <Textarea
                id="policy-content"
                value={content}
                onChange={(event) => setContent(event.target.value)}
                className="min-h-52 font-mono text-xs"
                required
              />
            </div>
            <div className="flex justify-end lg:col-span-2">
              <Button type="submit" disabled={create.isPending}>
                <Plus className="h-4 w-4" />
                {create.isPending ? "Creating…" : "Create policy version"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <section className="space-y-3">
        <div>
          <h2 className="text-foreground text-base font-semibold">Policy provenance</h2>
          <p className="text-muted-foreground text-sm">
            All versions remain visible; effective selection is deterministic and time-bounded.
          </p>
        </div>
        {policies.isLoading ? (
          <LoadingState variant="skeleton-list" rows={3} />
        ) : policies.isError ? (
          <Alert variant="destructive">
            <AlertTitle>Could not load policy versions</AlertTitle>
            <AlertDescription>{getErrorMessage(policies.error)}</AlertDescription>
          </Alert>
        ) : policies.data?.length ? (
          <div className="grid gap-4 xl:grid-cols-2">
            {policies.data.map((policy) => (
              <Card key={policy.id}>
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <CardTitle className="text-base">
                        {policy.policy_key} · v{policy.version}
                      </CardTitle>
                      <CardDescription>Created {formatDateTime(policy.created_at)}</CardDescription>
                    </div>
                    <Badge variant="outline">{policy.status}</Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <p>
                    <span className="text-muted-foreground">Effective: </span>
                    {formatDateTime(policy.valid_from)} –{" "}
                    {policy.valid_to ? formatDateTime(policy.valid_to) : "open"}
                  </p>
                  <pre className="bg-muted/40 max-h-52 overflow-auto rounded p-3 text-xs whitespace-pre-wrap">
                    {JSON.stringify(policy.content, null, 2)}
                  </pre>
                  <p className="font-mono text-xs break-all">
                    <span className="text-muted-foreground">SHA-256 </span>
                    {policy.content_hash}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <div className="border-border bg-card rounded-xl border px-6 py-12 text-center">
            <ScrollText className="text-muted-foreground mx-auto mb-3 h-8 w-8" />
            <p className="text-sm font-medium">No policy versions</p>
          </div>
        )}
      </section>
    </div>
  );
}
