import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, Clock, Plus, Trash2, Workflow } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, Checkbox, EmptyState, Field, Input, PageHeader, Select, Spinner, Textarea } from "@/components/ui";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import type { Sequence, SequenceStep, Template } from "@/types/api";

export function SequencesPage() {
  const qc = useQueryClient();
  const sequences = useQuery({ queryKey: ["sequences"], queryFn: () => api.get<Sequence[]>("/api/sequences") });
  const [editing, setEditing] = useState<Partial<Sequence> | null>(null);
  const [deleting, setDeleting] = useState<Sequence | null>(null);
  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/api/sequences/${id}`),
    onSuccess: () => {
      toast.success("Sequence deleted");
      setDeleting(null);
      qc.invalidateQueries({ queryKey: ["sequences"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <>
      <PageHeader
        title="Email sequences"
        description="An initial email followed by timed follow-ups. Sequences stop automatically on replies, opt-outs, suppressions, permanent bounces, pauses and cancellations."
        actions={
          <Button variant="primary" onClick={() => setEditing({ name: "", description: "", steps: [] })}>
            <Plus className="h-4 w-4" /> New sequence
          </Button>
        }
      />
      {sequences.isLoading ? (
        <Spinner />
      ) : !sequences.data?.length ? (
        <Card>
          <EmptyState icon={<Workflow className="h-6 w-6" />} title="No sequences yet" description="Create templates first, then chain them: initial email → wait → follow-up → wait → final follow-up." action={<Link to="/templates"><Button>Go to templates</Button></Link>} />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {sequences.data.map((s) => (
            <Card key={s.id} className="p-5">
              <div className="mb-3 flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-semibold">{s.name}</h3>
                  {s.description && <p className="text-sm text-muted">{s.description}</p>}
                  <p className="mt-1 text-xs text-faint">Used by {s.campaign_count} campaign(s)</p>
                </div>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => setEditing(s)}>
                    Edit
                  </Button>
                  <Button size="icon" variant="ghost" aria-label={`Delete ${s.name}`} onClick={() => setDeleting(s)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <ol className="space-y-1.5">
                {s.steps.map((st, i) => (
                  <li key={st.id ?? i}>
                    {i > 0 && (
                      <p className="flex items-center gap-1.5 py-1 pl-3 text-xs text-muted">
                        <Clock className="h-3 w-3" /> wait {st.delay_days}d{st.delay_hours ? ` ${st.delay_hours}h` : ""}
                      </p>
                    )}
                    <div className="flex items-center gap-2 rounded-lg border border-border bg-bg/50 px-3 py-2 text-sm">
                      <Badge tone={i === 0 ? "brand" : "neutral"}>{i === 0 ? "Initial" : `Follow-up ${i}`}</Badge>
                      <span className="truncate">{st.template_name}</span>
                      {i > 0 && st.send_as_reply && <span className="ml-auto text-xs text-faint">in thread</span>}
                    </div>
                  </li>
                ))}
              </ol>
            </Card>
          ))}
        </div>
      )}
      {editing && <SequenceEditor seq={editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(v) => !v && setDeleting(null)}
        title={`Delete "${deleting?.name}"?`}
        description="Approved campaigns keep their frozen copy of the steps."
        tone="danger"
        confirmLabel="Delete"
        loading={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.id)}
      />
    </>
  );
}

function SequenceEditor({ seq, onClose }: { seq: Partial<Sequence>; onClose: () => void }) {
  const qc = useQueryClient();
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Template[]>("/api/templates") });
  const [name, setName] = useState(seq.name ?? "");
  const [description, setDescription] = useState(seq.description ?? "");
  const [steps, setSteps] = useState<SequenceStep[]>(seq.steps?.length ? seq.steps : [{ template_id: 0, delay_days: 0, delay_hours: 0, send_as_reply: false }]);
  const save = useMutation({
    mutationFn: () => {
      const body = { name, description: description || null, steps: steps.map(({ template_id, delay_days, delay_hours, send_as_reply }) => ({ template_id, delay_days, delay_hours, send_as_reply })) };
      return seq.id ? api.put(`/api/sequences/${seq.id}`, body) : api.post("/api/sequences", body);
    },
    onSuccess: () => {
      toast.success("Sequence saved");
      qc.invalidateQueries({ queryKey: ["sequences"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const update = (i: number, patch: Partial<SequenceStep>) => setSteps(steps.map((s, j) => (i === j ? { ...s, ...patch } : s)));
  const invalid = !name.trim() || steps.some((s, i) => !s.template_id || (i > 0 && s.delay_days === 0 && s.delay_hours === 0));

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={seq.id ? "Edit sequence" : "New sequence"}
      size="lg"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={invalid} loading={save.isPending} onClick={() => save.mutate()}>
            Save sequence
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name" htmlFor="sqn">
          <Input id="sqn" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Description" htmlFor="sqd">
          <Textarea id="sqd" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        {templates.data?.length === 0 && <Alert tone="warn">Create at least one template first.</Alert>}
        <ol className="space-y-2">
          {steps.map((s, i) => (
            <li key={i}>
              {i > 0 && (
                <div className="flex flex-wrap items-center gap-2 py-2 pl-2 text-sm text-muted">
                  <ArrowDown className="h-4 w-4" /> Wait
                  <Input aria-label={`Step ${i + 1} delay days`} type="number" min={0} max={365} className="h-8 w-20" value={s.delay_days} onChange={(e) => update(i, { delay_days: Math.max(0, Number(e.target.value)) })} /> days
                  <Input aria-label={`Step ${i + 1} delay hours`} type="number" min={0} max={23} className="h-8 w-20" value={s.delay_hours} onChange={(e) => update(i, { delay_hours: Math.min(23, Math.max(0, Number(e.target.value))) })} /> hours
                  <span className="text-xs">(then the next allowed sending window)</span>
                </div>
              )}
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-bg/40 p-3">
                <Badge tone={i === 0 ? "brand" : "neutral"}>{i === 0 ? "Initial email" : `Follow-up ${i}`}</Badge>
                <Select aria-label={`Step ${i + 1} template`} className="min-w-[200px] flex-1" value={s.template_id || ""} onChange={(e) => update(i, { template_id: Number(e.target.value) })}>
                  <option value="">Choose template…</option>
                  {templates.data?.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </Select>
                {i > 0 && (
                  <label className="flex items-center gap-1.5 text-xs text-ink-2">
                    <Checkbox checked={s.send_as_reply} onChange={(e) => update(i, { send_as_reply: e.target.checked })} /> Reply in same thread
                  </label>
                )}
                {steps.length > 1 && (
                  <Button size="icon" variant="ghost" aria-label={`Remove step ${i + 1}`} onClick={() => setSteps(steps.filter((_, j) => j !== i))}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ol>
        {steps.length < 10 && (
          <Button size="sm" onClick={() => setSteps([...steps, { template_id: 0, delay_days: 3, delay_hours: 0, send_as_reply: true }])}>
            <Plus className="h-3.5 w-3.5" /> Add follow-up
          </Button>
        )}
      </div>
    </Dialog>
  );
}
