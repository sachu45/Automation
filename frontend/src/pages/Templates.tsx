import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, FileText, History, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { LeadPicker } from "@/components/LeadPicker";
import { Alert, Badge, Button, Card, CardBody, CardHeader, EmptyState, Field, Input, PageHeader, Select, Spinner, Tabs, Textarea } from "@/components/ui";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { AiWriter } from "@/features/ai/AiWriter";
import { api, errorMessage } from "@/lib/api";
import { cn, fmtDate } from "@/lib/utils";
import type { Lead, Preview, Template, TemplateVersion } from "@/types/api";

export function TemplatesPage() {
  const [tab, setTab] = useState<"templates" | "ai">("templates");
  return (
    <>
      <PageHeader title="Templates" description="Reusable, text-first emails with {{variables}}. Editing creates a new version; approved campaigns stay pinned to the version they were approved with." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "templates", label: "Templates" }, { value: "ai", label: "AI writing workspace" }]} />
      {tab === "templates" ? <TemplateWorkspace /> : <AiWriter />}
    </>
  );
}

interface Draft {
  id?: number;
  name: string;
  description: string;
  subject: string;
  body: string;
  format: "text" | "html";
}

const BLANK: Draft = {
  name: "",
  description: "",
  subject: "Quick question, {{first_name|there}}",
  body: "Hi {{first_name|there}},\n\n\n\n{{signature}}",
  format: "text",
};

function TemplateWorkspace() {
  const qc = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);
  const templates = useQuery({ queryKey: ["templates", showArchived], queryFn: () => api.get<Template[]>("/api/templates", { include_archived: showArchived }) });
  const vars = useQuery({ queryKey: ["template-vars"], queryFn: () => api.get<{ lead: string[]; sender: string[]; system: string[] }>("/api/templates/variables") });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [lead, setLead] = useState<Lead | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  const select = (t: Template) =>
    setDraft({ id: t.id, name: t.name, description: t.description ?? "", subject: t.current?.subject ?? "", body: t.current?.body ?? "", format: t.current?.format ?? "text" });

  // Debounced live preview
  useEffect(() => {
    if (!draft) return;
    const h = setTimeout(() => {
      api
        .post<Preview>("/api/templates/preview", { subject: draft.subject, body: draft.body, format: draft.format, lead_id: lead?.id ?? null })
        .then(setPreview)
        .catch((e) => setPreview({ subject: null, text: null, html: null, missing: [], errors: [errorMessage(e)], variables: [], unknown_variables: [], lead_id: null, sample_data: true }));
    }, 350);
    return () => clearTimeout(h);
  }, [draft?.subject, draft?.body, draft?.format, lead?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: (d: Draft) =>
      d.id
        ? api.patch<Template>(`/api/templates/${d.id}`, { name: d.name, description: d.description || null, subject: d.subject, body: d.body, format: d.format })
        : api.post<Template>("/api/templates", { name: d.name, description: d.description || null, subject: d.subject, body: d.body, format: d.format }),
    onSuccess: (t) => {
      toast.success(`Saved "${t.name}" (v${t.current?.version})`);
      qc.invalidateQueries({ queryKey: ["templates"] });
      select(t);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const archive = useMutation({
    mutationFn: (id: number) => api.patch(`/api/templates/${id}`, { archived: true }),
    onSuccess: () => {
      toast.success("Template archived");
      setArchiving(false);
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  function insert(v: string) {
    if (!draft) return;
    const el = bodyRef.current;
    const tag = `{{${v}}}`;
    if (!el) return setDraft({ ...draft, body: draft.body + tag });
    const start = el.selectionStart ?? draft.body.length;
    const next = draft.body.slice(0, start) + tag + draft.body.slice(el.selectionEnd ?? start);
    setDraft({ ...draft, body: next });
    requestAnimationFrame(() => {
      el.focus();
      el.selectionStart = el.selectionEnd = start + tag.length;
    });
  }

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[280px_1fr]">
      <Card className="h-fit">
        <div className="flex items-center justify-between border-b border-border p-3">
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Show archived
          </label>
          <Button size="sm" variant="primary" onClick={() => { setDraft({ ...BLANK }); setPreview(null); }}>
            <Plus className="h-3.5 w-3.5" /> New
          </Button>
        </div>
        {templates.isLoading ? (
          <Spinner />
        ) : !templates.data?.length ? (
          <EmptyState icon={<FileText className="h-6 w-6" />} title="No templates" description="Create one, or generate a draft in the AI workspace and save it as a template." />
        ) : (
          <ul className="max-h-[70vh] divide-y divide-border overflow-y-auto">
            {templates.data.map((t) => (
              <li key={t.id}>
                <button onClick={() => select(t)} className={cn("w-full px-4 py-3 text-left hover:bg-bg", draft?.id === t.id && "bg-brand-soft/60")}>
                  <p className="truncate text-sm font-medium text-ink">
                    {t.name} {t.archived && <Badge>archived</Badge>}
                  </p>
                  <p className="truncate text-xs text-muted">{t.current?.subject}</p>
                  <p className="mt-0.5 text-[11px] text-faint">
                    v{t.current?.version} · {fmtDate(t.updated_at, false)}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {!draft ? (
        <Card>
          <EmptyState title="Select a template or create a new one" description="Default to short, plain-text emails. Avoid tracking pixels, misleading subjects, and hidden content." />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-6 2xl:grid-cols-2">
          <Card>
            <CardHeader
              title={draft.id ? "Edit template" : "New template"}
              actions={
                <>
                  {draft.id && (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => setHistoryOpen(true)}>
                        <History className="h-3.5 w-3.5" /> Versions
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setArchiving(true)}>
                        <Archive className="h-3.5 w-3.5" /> Archive
                      </Button>
                    </>
                  )}
                  <Button size="sm" variant="primary" loading={save.isPending} disabled={!draft.name.trim() || !!preview?.errors.length} onClick={() => save.mutate(draft)}>
                    Save
                  </Button>
                </>
              }
            />
            <CardBody className="space-y-4">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_140px]">
                <Field label="Name" htmlFor="tn">
                  <Input id="tn" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                </Field>
                <Field label="Format" htmlFor="tf">
                  <Select id="tf" value={draft.format} onChange={(e) => setDraft({ ...draft, format: e.target.value as "text" | "html" })}>
                    <option value="text">Plain text</option>
                    <option value="html">Simple HTML</option>
                  </Select>
                </Field>
              </div>
              <Field label="Subject" htmlFor="ts" hint="Accurate and honest — no fake Re:/Fwd:">
                <Input id="ts" value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
              </Field>
              <Field label="Body" htmlFor="tb" hint={draft.format === "html" ? "HTML is sanitized; a plain-text version is generated automatically." : "An opt-out line is appended automatically unless you use {{opt_out_text}} or {{unsubscribe_url}}."}>
                <Textarea id="tb" ref={bodyRef} rows={14} className="font-mono text-[13px]" value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
              </Field>
              <div>
                <p className="mb-1.5 text-[13px] font-medium text-ink-2">Insert variable</p>
                <div className="flex flex-wrap gap-1.5">
                  {[...(vars.data?.lead ?? []), ...(vars.data?.sender ?? []), ...(vars.data?.system ?? [])].map((v) => (
                    <button key={v} onClick={() => insert(v)} className="rounded-md bg-bg px-2 py-0.5 font-mono text-xs text-ink-2 ring-1 ring-border hover:ring-brand">
                      {v}
                    </button>
                  ))}
                  <button onClick={() => insert("custom.field_name")} className="rounded-md bg-bg px-2 py-0.5 font-mono text-xs text-ink-2 ring-1 ring-border hover:ring-brand">
                    custom.…
                  </button>
                </div>
                <p className="mt-2 text-xs text-muted">
                  Add a fallback for optional fields: <code className="font-mono">{"{{first_name|there}}"}</code>. Messages with unresolved variables are never sent.
                </p>
              </div>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Preview" description={lead ? "Rendered with a real lead record" : "Rendered with sample data"} />
            <CardBody className="space-y-4">
              <LeadPicker value={lead} onChange={setLead} />
              {preview?.errors.length ? <Alert tone="danger" title="Syntax errors">{preview.errors.join("; ")}</Alert> : null}
              {preview?.missing.length ? (
                <Alert tone="warn" title="Missing values">
                  {preview.missing.join(", ")} — this lead would be excluded from campaigns until filled in or given a fallback.
                </Alert>
              ) : null}
              {preview?.unknown_variables.length ? <Alert tone="info">Looked up in custom fields: {preview.unknown_variables.join(", ")}</Alert> : null}
              {preview?.subject !== undefined && preview?.subject !== null && (
                <div className="rounded-lg border border-border">
                  <div className="border-b border-border px-4 py-2.5 text-sm">
                    <span className="text-muted">Subject: </span>
                    <span className="font-medium">{preview.subject}</span>
                  </div>
                  <pre className="whitespace-pre-wrap px-4 py-3 font-sans text-sm leading-relaxed text-ink-2">{preview.text}</pre>
                </div>
              )}
            </CardBody>
          </Card>
        </div>
      )}
      {draft?.id && <VersionHistory templateId={draft.id} open={historyOpen} onOpenChange={setHistoryOpen} onRestore={(v) => { setDraft({ ...draft, subject: v.subject, body: v.body, format: v.format }); setHistoryOpen(false); }} />}
      <ConfirmDialog
        open={archiving}
        onOpenChange={setArchiving}
        title="Archive this template?"
        description="It disappears from pickers. Campaigns already using it are unaffected."
        confirmLabel="Archive"
        loading={archive.isPending}
        onConfirm={() => draft?.id && archive.mutate(draft.id)}
      />
    </div>
  );
}

function VersionHistory({ templateId, open, onOpenChange, onRestore }: { templateId: number; open: boolean; onOpenChange: (v: boolean) => void; onRestore: (v: TemplateVersion) => void }) {
  const versions = useQuery({ queryKey: ["template-versions", templateId], queryFn: () => api.get<TemplateVersion[]>(`/api/templates/${templateId}/versions`), enabled: open });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Version history" description="Versions are immutable. Restoring loads an old version into the editor; saving creates a new version." size="lg">
      {versions.isLoading ? (
        <Spinner />
      ) : (
        <ul className="space-y-3">
          {versions.data?.map((v) => (
            <li key={v.id} className="rounded-lg border border-border p-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-sm font-semibold">
                  v{v.version} <span className="font-normal text-muted">· {fmtDate(v.created_at)}</span>
                </span>
                <Button size="sm" onClick={() => onRestore(v)}>
                  Load into editor
                </Button>
              </div>
              <p className="text-sm font-medium">{v.subject}</p>
              <pre className="mt-1 max-h-32 overflow-hidden whitespace-pre-wrap font-sans text-xs text-muted">{v.body}</pre>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
