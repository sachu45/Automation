import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileUp } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, CardBody, CardHeader, Field, Input, PageHeader, Select, StatCard, Table, Td, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { LeadList } from "@/types/api";

interface Upload {
  import_id: number;
  filename: string;
  headers: string[];
  row_count: number;
  suggested_mapping: Record<string, string>;
  sample_rows: string[][];
  target_fields: string[];
}
interface Row {
  line: number;
  status: string;
  email: string | null;
  errors: string[];
}
interface PreviewResult {
  summary: Record<string, number>;
  sample: Row[];
  problems: Row[];
}

const STATUS_TONE: Record<string, "ok" | "danger" | "warn" | "neutral" | "info"> = {
  valid: "ok",
  invalid: "danger",
  duplicate_in_file: "warn",
  existing: "info",
  suppressed: "danger",
};

export function LeadImportPage() {
  const qc = useQueryClient();
  const [upload, setUpload] = useState<Upload | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [source, setSource] = useState("");
  const [tags, setTags] = useState("");
  const [listId, setListId] = useState("");
  const [existing, setExisting] = useState<"skip" | "fill_blanks">("skip");
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [done, setDone] = useState<Record<string, number> | null>(null);
  const lists = useQuery({ queryKey: ["lists"], queryFn: () => api.get<LeadList[]>("/api/lists") });

  const step = done ? 4 : preview ? 3 : upload ? 2 : 1;
  const body = () => ({ mapping, source: source || null, tags: tags.split(",").map((t) => t.trim()).filter(Boolean), list_id: listId ? Number(listId) : null, existing });

  const uploadMut = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api.upload<Upload>("/api/leads/import/upload", fd);
    },
    onSuccess: (u) => {
      setUpload(u);
      setMapping(u.suggested_mapping);
      setPreview(null);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const previewMut = useMutation({
    mutationFn: () => api.post<PreviewResult>(`/api/leads/import/${upload!.import_id}/preview`, body()),
    onSuccess: setPreview,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const commitMut = useMutation({
    mutationFn: () => api.post<{ summary: Record<string, number> }>(`/api/leads/import/${upload!.import_id}/commit`, body()),
    onSuccess: (r) => {
      setDone(r.summary);
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["lists"] });
      toast.success(`Imported ${r.summary.created} lead(s)`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const reset = () => {
    setUpload(null);
    setPreview(null);
    setDone(null);
  };

  const targetOptions = upload ? ["ignore", ...upload.target_fields] : [];

  return (
    <>
      <Link to="/leads" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
        <ArrowLeft className="h-4 w-4" /> Leads
      </Link>
      <PageHeader title="Import leads from CSV" description="Only import contacts you are permitted to use. Existing leads are never silently overwritten; suppressed addresses are blocked." />
      <ol className="mb-6 flex flex-wrap gap-2 text-sm">
        {["Upload", "Map fields", "Preview", "Done"].map((s, i) => (
          <li key={s} className={cn("flex items-center gap-2 rounded-full px-3 py-1", step === i + 1 ? "bg-brand text-white" : step > i + 1 ? "bg-brand-soft text-brand" : "bg-surface text-muted ring-1 ring-border")}>
            <span className="tabular font-semibold">{i + 1}</span> {s}
          </li>
        ))}
      </ol>

      {step === 1 && (
        <Card>
          <CardBody>
            <label className="flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed border-border-strong px-6 py-14 text-center hover:border-brand hover:bg-brand-soft/30">
              <FileUp className="h-8 w-8 text-muted" />
              <div>
                <p className="font-medium">Choose a CSV file</p>
                <p className="text-sm text-muted">UTF-8 recommended · max 10 MB · up to 50,000 rows · first row must be headers</p>
              </div>
              <input type="file" accept=".csv,text/csv" className="sr-only" aria-label="CSV file" onChange={(e) => e.target.files?.[0] && uploadMut.mutate(e.target.files[0])} />
              {uploadMut.isPending && <span className="text-sm text-muted">Uploading…</span>}
            </label>
          </CardBody>
        </Card>
      )}

      {step >= 2 && upload && !done && (
        <Card>
          <CardHeader title={`Map columns — ${upload.filename}`} description={`${upload.row_count.toLocaleString()} rows detected. Unmapped columns are ignored; map extra columns to custom fields.`} actions={<Button size="sm" variant="ghost" onClick={reset}>Choose another file</Button>} />
          <Table>
            <thead>
              <tr>
                <Th>CSV column</Th>
                <Th>Sample values</Th>
                <Th>Import as</Th>
              </tr>
            </thead>
            <tbody>
              {upload.headers.map((h, idx) => {
                const v = mapping[h] ?? "ignore";
                const isCustom = v.startsWith("custom:");
                return (
                  <tr key={h}>
                    <Td className="font-medium text-ink">{h}</Td>
                    <Td className="max-w-[280px] truncate text-xs text-muted">{upload.sample_rows.map((r) => r[idx]).filter(Boolean).slice(0, 3).join(" · ")}</Td>
                    <Td>
                      <div className="flex gap-2">
                        <Select
                          aria-label={`Mapping for ${h}`}
                          value={isCustom ? "custom" : v}
                          onChange={(e) => {
                            setPreview(null);
                            const val = e.target.value === "custom" ? `custom:${h.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "")}` : e.target.value;
                            setMapping({ ...mapping, [h]: val });
                          }}
                        >
                          {targetOptions.map((t) => (
                            <option key={t} value={t}>
                              {t === "ignore" ? "— ignore —" : t.replace(/_/g, " ")}
                            </option>
                          ))}
                          <option value="custom">custom field…</option>
                        </Select>
                        {isCustom && (
                          <Input
                            aria-label={`Custom field name for ${h}`}
                            className="w-40 font-mono text-[13px]"
                            value={v.slice(7)}
                            onChange={(e) => {
                              setPreview(null);
                              setMapping({ ...mapping, [h]: `custom:${e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "")}` });
                            }}
                          />
                        )}
                      </div>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <CardBody className="grid grid-cols-1 gap-4 border-t border-border sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Lead source label" htmlFor="src" hint="e.g. the event or directory name">
              <Input id="src" value={source} onChange={(e) => { setSource(e.target.value); setPreview(null); }} />
            </Field>
            <Field label="Add tags" htmlFor="tags" hint="Comma separated">
              <Input id="tags" value={tags} onChange={(e) => { setTags(e.target.value); setPreview(null); }} />
            </Field>
            <Field label="Add to list" htmlFor="list">
              <Select id="list" value={listId} onChange={(e) => setListId(e.target.value)}>
                <option value="">No list</option>
                {lists.data?.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Existing leads" htmlFor="existing" hint="Existing values are never overwritten">
              <Select id="existing" value={existing} onChange={(e) => setExisting(e.target.value as "skip" | "fill_blanks")}>
                <option value="skip">Skip (leave untouched)</option>
                <option value="fill_blanks">Fill empty fields only</option>
              </Select>
            </Field>
          </CardBody>
          <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
            <Button variant="primary" onClick={() => previewMut.mutate()} loading={previewMut.isPending}>
              Validate & preview
            </Button>
          </div>
        </Card>
      )}

      {preview && !done && (
        <Card className="mt-6">
          <CardHeader title="Preview" description="Nothing has been imported yet." />
          <CardBody>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
              <StatCard label="Rows" value={preview.summary.total_rows} />
              <StatCard label="Valid (new)" value={preview.summary.valid} tone="ok" />
              <StatCard label="Invalid" value={preview.summary.invalid} tone={preview.summary.invalid ? "danger" : undefined} />
              <StatCard label="Duplicate in file" value={preview.summary.duplicate_in_file} />
              <StatCard label="Already exist" value={preview.summary.existing} />
              <StatCard label="Suppressed (blocked)" value={preview.summary.suppressed} tone={preview.summary.suppressed ? "danger" : undefined} />
            </div>
            {preview.problems.length > 0 && (
              <div className="mt-5">
                <h3 className="mb-2 text-sm font-semibold">Rows that won't be created</h3>
                <div className="max-h-80 overflow-auto rounded-lg border border-border">
                  <Table>
                    <thead>
                      <tr>
                        <Th>Line</Th>
                        <Th>Email</Th>
                        <Th>Result</Th>
                        <Th>Reason</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.problems.map((r) => (
                        <tr key={r.line}>
                          <Td className="tabular">{r.line}</Td>
                          <Td>{r.email ?? "—"}</Td>
                          <Td>
                            <Badge tone={STATUS_TONE[r.status]}>{r.status.replace(/_/g, " ")}</Badge>
                          </Td>
                          <Td className="text-xs">{r.errors.join("; ") || (r.status === "existing" ? (existing === "skip" ? "Kept as-is" : "Empty fields will be filled") : "")}</Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              </div>
            )}
          </CardBody>
          <div className="flex justify-end gap-2 border-t border-border px-5 py-3">
            <Button variant="primary" onClick={() => commitMut.mutate()} loading={commitMut.isPending} disabled={!preview.summary.valid && !preview.summary.existing}>
              Import {preview.summary.valid} new lead(s)
            </Button>
          </div>
        </Card>
      )}

      {done && (
        <Card>
          <CardBody className="space-y-4">
            <Alert tone="ok" title="Import complete">
              {done.created} created · {done.updated_blanks ?? 0} existing leads had empty fields filled · {done.added_to_list ?? 0} added to list · {done.suppressed} suppressed blocked · {done.invalid} invalid skipped.
            </Alert>
            <div className="flex gap-2">
              <Link to="/leads">
                <Button variant="primary">View leads</Button>
              </Link>
              <Button onClick={reset}>Import another file</Button>
            </div>
          </CardBody>
        </Card>
      )}
    </>
  );
}
