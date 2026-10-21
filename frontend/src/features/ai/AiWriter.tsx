import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, RefreshCw, Save, Sparkles, Wand2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { LeadPicker } from "@/components/LeadPicker";
import { Alert, Badge, Button, Card, CardBody, CardHeader, EmptyState, Field, Input, Select, Switch, Textarea } from "@/components/ui";
import { Dialog } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { cn, fmtDate } from "@/lib/utils";
import type { AiDraft, AiStatus, Lead } from "@/types/api";

interface Inputs {
  product_description: string;
  ideal_customer_profile: string;
  target_audience: string;
  reason: string;
  value_proposition: string;
  call_to_action: string;
  tone: "friendly" | "professional" | "direct" | "casual";
  length: "short" | "medium" | "long";
  prospect_context: string;
  use_variables: boolean;
  segments: string;
}

const EMPTY: Inputs = {
  product_description: "",
  ideal_customer_profile: "",
  target_audience: "",
  reason: "",
  value_proposition: "",
  call_to_action: "",
  tone: "friendly",
  length: "short",
  prospect_context: "",
  use_variables: true,
  segments: "",
};

export function AiWriter() {
  const qc = useQueryClient();
  const [inputs, setInputs] = useState<Inputs>(EMPTY);
  const [lead, setLead] = useState<Lead | null>(null);
  const [draft, setDraft] = useState<AiDraft | null>(null);
  const [compare, setCompare] = useState<AiDraft | null>(null);
  const [savePromptOpen, setSavePromptOpen] = useState(false);
  const [saveTplOpen, setSaveTplOpen] = useState(false);
  const [name, setName] = useState("");
  const status = useQuery({ queryKey: ["ai-status"], queryFn: () => api.get<AiStatus>("/api/ai/status") });
  const prompts = useQuery({ queryKey: ["ai-prompts"], queryFn: () => api.get<{ id: number; name: string; inputs: Inputs }[]>("/api/ai/prompts") });
  const recent = useQuery({ queryKey: ["ai-drafts"], queryFn: () => api.get<AiDraft[]>("/api/ai/drafts", { limit: 10 }) });

  const payload = () => ({
    ...inputs,
    segments: inputs.segments.split(",").map((s) => s.trim()).filter(Boolean),
    lead_id: lead?.id ?? null,
  });

  const refreshDraft = async (id: number) => setDraft(await api.get<AiDraft>(`/api/ai/drafts/${id}`));

  const generate = useMutation({
    mutationFn: () => api.post<AiDraft>("/api/ai/generate", payload()),
    onSuccess: (d) => {
      refreshDraft(d.id);
      qc.invalidateQueries({ queryKey: ["ai-drafts"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const regen = useMutation({
    mutationFn: (id: number) => api.post<AiDraft>(`/api/ai/drafts/${id}/regenerate`),
    onSuccess: (d) => {
      refreshDraft(d.id);
      qc.invalidateQueries({ queryKey: ["ai-drafts"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const saveDraft = useMutation({
    mutationFn: (d: AiDraft) => api.patch<AiDraft>(`/api/ai/drafts/${d.id}`, { subject: d.subject, body: d.body }),
    onSuccess: (d) => {
      toast.success("Draft saved");
      refreshDraft(d.id);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const approve = useMutation({
    mutationFn: async (d: AiDraft) => {
      await api.patch(`/api/ai/drafts/${d.id}`, { subject: d.subject, body: d.body });
      return api.post<AiDraft>(`/api/ai/drafts/${d.id}/approve`);
    },
    onSuccess: (d) => {
      toast.success("Draft approved — it can now be used for a send or saved as a template");
      refreshDraft(d.id);
      qc.invalidateQueries({ queryKey: ["ai-drafts"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const savePrompt = useMutation({
    mutationFn: () => api.post("/api/ai/prompts", { name, inputs: { ...payload(), lead_id: null } }),
    onSuccess: () => {
      toast.success("Prompt saved");
      setSavePromptOpen(false);
      qc.invalidateQueries({ queryKey: ["ai-prompts"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const saveTemplate = useMutation({
    mutationFn: (d: AiDraft) => api.post(`/api/ai/drafts/${d.id}/save-template`, { name }),
    onSuccess: () => {
      toast.success("Saved as template");
      setSaveTplOpen(false);
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const set = <K extends keyof Inputs>(k: K, v: Inputs[K]) => setInputs({ ...inputs, [k]: v });
  const edit = (patch: Partial<AiDraft>) => draft && setDraft({ ...draft, ...patch, status: "draft" });

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-[420px_1fr]">
      <Card className="h-fit">
        <CardHeader
          title="Brief"
          description="Drafts may only use what you write here and what's on the lead record."
          actions={
            prompts.data?.length ? (
              <Select
                aria-label="Load saved prompt"
                className="h-8 w-40 text-[13px]"
                value=""
                onChange={(e) => {
                  const p = prompts.data.find((x) => x.id === Number(e.target.value));
                  if (p) setInputs({ ...EMPTY, ...p.inputs, segments: Array.isArray(p.inputs.segments) ? (p.inputs.segments as unknown as string[]).join(", ") : p.inputs.segments ?? "" });
                }}
              >
                <option value="">Saved prompts…</option>
                {prompts.data.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            ) : undefined
          }
        />
        <CardBody className="space-y-3.5">
          {status.data && (
            <Alert tone={status.data.ai_enabled ? "ok" : "info"}>
              {status.data.ai_enabled ? (
                <>
                  Using <b>{status.data.model}</b> via an OpenAI-compatible API.
                </>
              ) : (
                <>Template mode (no AI key configured): drafts are assembled deterministically from your brief. Configure AI in Settings.</>
              )}
            </Alert>
          )}
          <Field label="Product or service" htmlFor="ai-p">
            <Textarea id="ai-p" rows={2} value={inputs.product_description} onChange={(e) => set("product_description", e.target.value)} />
          </Field>
          <Field label="Ideal customer profile" htmlFor="ai-icp">
            <Textarea id="ai-icp" rows={2} value={inputs.ideal_customer_profile} onChange={(e) => set("ideal_customer_profile", e.target.value)} />
          </Field>
          <Field label="Target audience" htmlFor="ai-ta">
            <Input id="ai-ta" value={inputs.target_audience} onChange={(e) => set("target_audience", e.target.value)} placeholder="e.g. heads of operations at clinics" />
          </Field>
          <Field label="Reason for reaching out" htmlFor="ai-r">
            <Input id="ai-r" value={inputs.reason} onChange={(e) => set("reason", e.target.value)} />
          </Field>
          <Field label="Value proposition" htmlFor="ai-v">
            <Textarea id="ai-v" rows={2} value={inputs.value_proposition} onChange={(e) => set("value_proposition", e.target.value)} />
          </Field>
          <Field label="Call to action" htmlFor="ai-c">
            <Input id="ai-c" value={inputs.call_to_action} onChange={(e) => set("call_to_action", e.target.value)} placeholder="Would a 15-minute call next week be useful?" />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Tone" htmlFor="ai-tone">
              <Select id="ai-tone" value={inputs.tone} onChange={(e) => set("tone", e.target.value as Inputs["tone"])}>
                {["friendly", "professional", "direct", "casual"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </Select>
            </Field>
            <Field label="Length" htmlFor="ai-len">
              <Select id="ai-len" value={inputs.length} onChange={(e) => set("length", e.target.value as Inputs["length"])}>
                {["short", "medium", "long"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </Select>
            </Field>
          </div>
          <LeadPicker value={lead} onChange={setLead} label="Prospect (optional)" />
          <Field label="Additional prospect context" htmlFor="ai-ctx" hint="Only facts you've verified. Leave blank rather than guess.">
            <Textarea id="ai-ctx" rows={3} value={inputs.prospect_context} onChange={(e) => set("prospect_context", e.target.value)} />
          </Field>
          <Field label="Segment variations" htmlFor="ai-seg" hint="Optional, comma separated (e.g. dental clinics, physio practices)">
            <Input id="ai-seg" value={inputs.segments} onChange={(e) => set("segments", e.target.value)} />
          </Field>
          <div className="flex items-center justify-between gap-3 rounded-lg bg-bg px-3 py-2.5">
            <div>
              <p className="text-sm font-medium">Reusable template</p>
              <p className="text-xs text-muted">Write with {"{{first_name}}"}-style variables instead of a specific person</p>
            </div>
            <Switch checked={inputs.use_variables} onChange={(v) => set("use_variables", v)} label="Write as reusable template" />
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <Button variant="primary" onClick={() => generate.mutate()} loading={generate.isPending}>
              <Sparkles className="h-4 w-4" /> Generate draft
            </Button>
            <Button onClick={() => { setName(""); setSavePromptOpen(true); }}>
              <Save className="h-4 w-4" /> Save prompt
            </Button>
          </div>
        </CardBody>
      </Card>

      <div className="space-y-6">
        {!draft ? (
          <Card>
            <EmptyState icon={<Wand2 className="h-6 w-6" />} title="No draft yet" description="Fill in the brief and generate. You review and edit everything; nothing is sent from here." />
            {!!recent.data?.length && (
              <div className="border-t border-border px-5 py-3">
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">Recent drafts</p>
                <ul className="space-y-1">
                  {recent.data.map((d) => (
                    <li key={d.id}>
                      <button className="text-sm hover:text-brand" onClick={() => refreshDraft(d.id)}>
                        #{d.id} {d.subject} <span className="text-xs text-muted">· {d.status} · {fmtDate(d.created_at)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Card>
        ) : (
          <Card>
            <CardHeader
              title={
                <span className="flex items-center gap-2">
                  Draft #{draft.id} {draft.label && <Badge>{draft.label}</Badge>}
                  {draft.status === "approved" ? <Badge tone="ok">approved</Badge> : <Badge tone="warn">needs approval</Badge>}
                </span>
              }
              description={`via ${draft.provider} · ${fmtDate(draft.created_at)}`}
              actions={
                <>
                  <Button size="sm" onClick={() => regen.mutate(draft.id)} loading={regen.isPending}>
                    <RefreshCw className="h-3.5 w-3.5" /> Regenerate
                  </Button>
                  <Button size="sm" onClick={() => saveDraft.mutate(draft)} loading={saveDraft.isPending}>
                    Save edits
                  </Button>
                  {draft.status === "approved" ? (
                    <Button size="sm" variant="primary" onClick={() => { setName(draft.subject.slice(0, 60)); setSaveTplOpen(true); }}>
                      Save as template
                    </Button>
                  ) : (
                    <Button size="sm" variant="primary" onClick={() => approve.mutate(draft)} loading={approve.isPending}>
                      <Check className="h-3.5 w-3.5" /> Approve
                    </Button>
                  )}
                </>
              }
            />
            <CardBody className="space-y-4">
              {draft.output.warnings?.length > 0 && (
                <Alert tone="warn" title="Review before approving">
                  <ul className="list-disc pl-4">
                    {draft.output.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                </Alert>
              )}
              <Field label="Subject" htmlFor="d-subj">
                <Input id="d-subj" value={draft.subject} onChange={(e) => edit({ subject: e.target.value })} />
              </Field>
              <div className="flex flex-wrap gap-1.5">
                {draft.output.subject_lines.map((s) => (
                  <button key={s} onClick={() => edit({ subject: s })} className={cn("rounded-full px-2.5 py-1 text-xs ring-1", s === draft.subject ? "bg-brand-soft text-brand ring-brand/30" : "bg-bg text-ink-2 ring-border hover:ring-brand")}>
                    {s}
                  </button>
                ))}
              </div>
              <Field label="Body" htmlFor="d-body">
                <Textarea id="d-body" rows={12} value={draft.body} onChange={(e) => edit({ body: e.target.value })} />
              </Field>
              {draft.output.opening_lines.length > 0 && (
                <Section title="Alternative openings">
                  {draft.output.opening_lines.map((o) => (
                    <Alt key={o} text={o} onUse={() => {
                      const lines = draft.body.split("\n\n");
                      if (lines.length > 1) lines[1] = o;
                      edit({ body: lines.join("\n\n") });
                    }} />
                  ))}
                </Section>
              )}
              <Section title="Shorter / longer">
                {draft.output.short_version && <Alt label="Short" text={draft.output.short_version} onUse={() => edit({ body: draft.output.short_version })} />}
                {draft.output.long_version && <Alt label="Long" text={draft.output.long_version} onUse={() => edit({ body: draft.output.long_version })} />}
              </Section>
              {draft.output.follow_ups.length > 0 && (
                <Section title="Follow-up drafts" hint="Save these as templates and add them as sequence steps.">
                  {draft.output.follow_ups.map((f, i) => (
                    <Alt key={i} label={`Follow-up ${i + 1}`} text={f.body} />
                  ))}
                </Section>
              )}
              {draft.output.segment_variations.length > 0 && (
                <Section title="Segment variations">
                  {draft.output.segment_variations.map((v) => (
                    <Alt key={v.segment} label={v.segment} text={v.body} onUse={() => edit({ body: v.body })} />
                  ))}
                </Section>
              )}
              {draft.output.notes && <p className="text-xs text-muted">{draft.output.notes}</p>}
              {draft.versions && draft.versions.length > 1 && (
                <Section title="Versions">
                  <div className="flex flex-wrap gap-2">
                    {draft.versions.map((v) => (
                      <Button key={v.id} size="sm" variant={v.id === draft.id ? "secondary" : "outline"} onClick={() => (v.id === draft.id ? null : api.get<AiDraft>(`/api/ai/drafts/${v.id}`).then(setCompare))}>
                        {v.label ?? `#${v.id}`} {v.status === "approved" && "✓"}
                      </Button>
                    ))}
                  </div>
                </Section>
              )}
            </CardBody>
          </Card>
        )}
      </div>

      <Dialog open={!!compare} onOpenChange={(v) => !v && setCompare(null)} title="Compare versions" size="xl"
        footer={<><Button onClick={() => setCompare(null)}>Close</Button><Button variant="primary" onClick={() => { if (compare) refreshDraft(compare.id); setCompare(null); }}>Open {compare?.label}</Button></>}>
        {compare && draft && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {[draft, compare].map((d) => (
              <div key={d.id} className="rounded-lg border border-border p-3">
                <p className="mb-1 text-xs font-medium uppercase text-muted">{d.label ?? `#${d.id}`}</p>
                <p className="text-sm font-semibold">{d.subject}</p>
                <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-ink-2">{d.body}</pre>
              </div>
            ))}
          </div>
        )}
      </Dialog>
      <Dialog open={savePromptOpen} onOpenChange={setSavePromptOpen} title="Save prompt" size="sm"
        footer={<><Button onClick={() => setSavePromptOpen(false)}>Cancel</Button><Button variant="primary" disabled={!name.trim()} loading={savePrompt.isPending} onClick={() => savePrompt.mutate()}>Save</Button></>}>
        <Field label="Name" htmlFor="pn"><Input id="pn" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></Field>
      </Dialog>
      <Dialog open={saveTplOpen} onOpenChange={setSaveTplOpen} title="Save as template" size="sm"
        footer={<><Button onClick={() => setSaveTplOpen(false)}>Cancel</Button><Button variant="primary" disabled={!name.trim()} loading={saveTemplate.isPending} onClick={() => draft && saveTemplate.mutate(draft)}>Save</Button></>}>
        <Field label="Template name" htmlFor="tpn"><Input id="tpn" value={name} onChange={(e) => setName(e.target.value)} autoFocus /></Field>
      </Dialog>
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[13px] font-medium text-ink-2">{title}</p>
      {hint && <p className="text-xs text-muted">{hint}</p>}
      <div className="mt-2 space-y-2">{children}</div>
    </div>
  );
}

function Alt({ label, text, onUse }: { label?: string; text: string; onUse?: () => void }) {
  return (
    <div className="rounded-lg border border-border bg-bg/40 p-3">
      <div className="mb-1 flex items-center justify-between gap-2">
        {label ? <span className="text-xs font-medium uppercase tracking-wide text-muted">{label}</span> : <span />}
        {onUse && (
          <Button size="sm" variant="ghost" onClick={onUse}>
            Use
          </Button>
        )}
      </div>
      <pre className="whitespace-pre-wrap font-sans text-sm text-ink-2">{text}</pre>
    </div>
  );
}
