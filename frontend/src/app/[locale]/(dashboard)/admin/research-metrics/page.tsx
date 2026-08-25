"use client";

import { type FormEvent, type ReactNode, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DatabaseZap, FileCheck2, RefreshCw, Upload } from "lucide-react";
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
  Checkbox,
  Input,
  Label,
  Textarea,
} from "@/components/ui";
import { literatureResearchApi } from "@/lib/literature-research-api";
import { formatDate, formatDateTime, getErrorMessage } from "@/lib/utils";
import type { ResearchMetricSnapshot } from "@/types/literature-research";

const MAX_FILE_BYTES = 20 * 1024 * 1024;

export default function ResearchMetricsAdminPage() {
  const queryClient = useQueryClient();
  const [licenseAttested, setLicenseAttested] = useState(false);

  const snapshots = useQuery({
    queryKey: ["admin", "research-metric-snapshots"],
    queryFn: literatureResearchApi.listMetricSnapshots,
  });

  const importSnapshot = useMutation({
    mutationFn: literatureResearchApi.importMetricSnapshot,
    onSuccess: async (snapshot) => {
      toast.success(`${snapshot.source_name} ${snapshot.source_version} imported`);
      setLicenseAttested(false);
      await queryClient.invalidateQueries({
        queryKey: ["admin", "research-metric-snapshots"],
      });
    },
    onError: (error) => toast.error(getErrorMessage(error, "Metric import failed")),
  });

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = new FormData(form);
    const file = payload.get("file");
    if (!(file instanceof File) || file.size === 0) {
      toast.error("Choose a non-empty CSV file");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      toast.error("CSV must be 20 MiB or smaller");
      return;
    }
    if (!licenseAttested) {
      toast.error("Confirm that your organization is authorized to use this data");
      return;
    }
    payload.set("license_attested", "true");
    if (!payload.get("effective_to")) payload.delete("effective_to");
    importSnapshot.mutate(payload, {
      onSuccess: () => form.reset(),
    });
  };

  return (
    <div className="space-y-6">
      <Alert>
        <FileCheck2 className="h-4 w-4" />
        <AlertTitle>Licensed data only</AlertTitle>
        <AlertDescription>
          Upload an authorized JCR, final CAS, CCF, CORE, or institutional snapshot. The system
          records its license scope, effective window, immutable SHA-256, and private object key;
          it never scrapes unofficial metric websites.
        </AlertDescription>
      </Alert>

      <Card>
        <CardHeader>
          <CardTitle>Import metric snapshot</CardTitle>
          <CardDescription>
            Required CSV columns: venue_name, venue_type, metric_name, metric_value, metric_year.
            Optional: issn_l.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="grid gap-4 lg:grid-cols-2" onSubmit={submit}>
            <Field label="Authorized CSV" htmlFor="metric-file">
              <Input id="metric-file" name="file" type="file" accept=".csv,text/csv" required />
            </Field>
            <Field label="Source name" htmlFor="source-name">
              <Input id="source-name" name="source_name" placeholder="JCR / CAS / CCF" required />
            </Field>
            <Field label="Source version" htmlFor="source-version">
              <Input id="source-version" name="source_version" placeholder="2025" required />
            </Field>
            <Field label="Effective from" htmlFor="effective-from">
              <Input id="effective-from" name="effective_from" type="date" required />
            </Field>
            <Field label="Effective to (optional)" htmlFor="effective-to">
              <Input id="effective-to" name="effective_to" type="date" />
            </Field>
            <Field label="License reference" htmlFor="license-reference">
              <Input
                id="license-reference"
                name="license_reference"
                placeholder="Contract, subscription, or internal record"
                required
              />
            </Field>
            <div className="lg:col-span-2">
              <Field label="Authorized scope" htmlFor="authorized-scope">
                <Textarea
                  id="authorized-scope"
                  name="authorized_scope"
                  placeholder="Who may use the snapshot and for which workflows"
                  required
                />
              </Field>
            </div>
            <label className="border-border bg-muted/30 flex items-start gap-3 rounded-lg border p-3 lg:col-span-2">
              <Checkbox
                checked={licenseAttested}
                onCheckedChange={(value) => setLicenseAttested(value === true)}
                aria-label="License attestation"
              />
              <span className="text-sm leading-5">
                I attest that this organization is authorized to upload and use this snapshot
                within the scope stated above.
              </span>
            </label>
            <div className="flex justify-end lg:col-span-2">
              <Button type="submit" disabled={importSnapshot.isPending || !licenseAttested}>
                <Upload className="h-4 w-4" />
                {importSnapshot.isPending ? "Importing…" : "Import authorized snapshot"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-foreground text-base font-semibold">Metric provenance</h2>
            <p className="text-muted-foreground text-sm">
              Effective versions used by the fail-closed constraint engine.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => snapshots.refetch()}>
            <RefreshCw className={snapshots.isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            Refresh
          </Button>
        </div>

        {snapshots.isLoading ? (
          <LoadingState variant="skeleton-list" rows={3} />
        ) : snapshots.isError ? (
          <Alert variant="destructive">
            <AlertTitle>Could not load metric snapshots</AlertTitle>
            <AlertDescription>{getErrorMessage(snapshots.error)}</AlertDescription>
          </Alert>
        ) : snapshots.data?.length ? (
          <div className="grid gap-4 xl:grid-cols-2">
            {snapshots.data.map((snapshot) => (
              <SnapshotCard key={snapshot.id} snapshot={snapshot} />
            ))}
          </div>
        ) : (
          <div className="border-border bg-card rounded-xl border px-6 py-12 text-center">
            <DatabaseZap className="text-muted-foreground mx-auto mb-3 h-8 w-8" />
            <p className="text-foreground text-sm font-medium">No authorized snapshot imported</p>
            <p className="text-muted-foreground mt-1 text-xs">
              Missing metrics remain UNKNOWN and cannot enter the strict result set.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

function SnapshotCard({ snapshot }: { snapshot: ResearchMetricSnapshot }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">
              {snapshot.source_name} · {snapshot.source_version}
            </CardTitle>
            <CardDescription>Imported {formatDateTime(snapshot.imported_at)}</CardDescription>
          </div>
          <Badge variant={snapshot.status === "REVOKED" ? "destructive" : "outline"}>
            {snapshot.status}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-[9rem_1fr]">
          <dt className="text-muted-foreground">Metrics</dt>
          <dd>{snapshot.metric_names.join(", ")}</dd>
          <dt className="text-muted-foreground">Effective window</dt>
          <dd>
            {formatDate(snapshot.effective_from)} –{" "}
            {snapshot.effective_to ? formatDate(snapshot.effective_to) : "open"}
          </dd>
          <dt className="text-muted-foreground">License reference</dt>
          <dd className="break-words">{snapshot.license_reference}</dd>
          <dt className="text-muted-foreground">Authorized scope</dt>
          <dd className="break-words">{snapshot.authorized_scope}</dd>
          <dt className="text-muted-foreground">Payload SHA-256</dt>
          <dd className="font-mono text-xs break-all">{snapshot.payload_sha256}</dd>
          <dt className="text-muted-foreground">Private object</dt>
          <dd className="font-mono text-xs break-all">{snapshot.object_key}</dd>
        </dl>
      </CardContent>
    </Card>
  );
}
