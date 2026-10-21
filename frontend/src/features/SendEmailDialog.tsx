import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Alert, Button, Field, Input, Select, Textarea } from "@/components/ui";
import { Dialog } from "@/components/ui/dialog";
import { useAppSettings } from "@/app/Layout";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api";
import type { Account, AiDraft, Lead, Preview, Template } from "@/types/api";

/** Individual send: pick content, preview the final rendered message, confirm. Always queued for the worker. */
export function SendEmailDialog({ lead, open, onOpenChange }: { lead: Lead; open: boolean; onOpenChange: (v: boolean) => void }) {
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api.get<Account[]>("/api/accounts"), enabled: open });
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Template[]>("/api/templates"), enabled: open });
  const drafts = useQuery({ queryKey: ["ai-drafts", "approved"], queryFn: () => api.get<AiDraft[]>("/api/ai/drafts", { status: "approved" }), enabled: open });
  const settings = useAppSettings();
  const [accountId, setAccountId] = useState<number | "">("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [draftId, setDraftId] = useState<number | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const key = useMemo(() => newIdempotencyKey(), [open]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (accounts.data?.length && accountId === "") setAccountId(accounts.data[0].id);
  }, [accounts.data, accountId]);
  useEffect(() => setPreview(null), [subject, body, accountId]);

  const previewMut = useMutation({
    mutationFn: () => api.post<Preview>("/api/sends/preview", { lead_id: lead.id, account_id: accountId, subject, body, idempotency_key: key }),
    onSuccess: setPreview,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const send = useMutation({
    mutationFn: () => api.post<{ simulation: boolean }>("/api/sends", { lead_id: lead.id, account_id: accountId, subject, body, draft_id: draftId, idempotency_key: key, confirm: true }),
    onSuccess: (r) => {
      toast.success(r.simulation ? "Queued as a dry-run simulation (nothing will be sent)" : "Queued for sending by the worker");
      onOpenChange(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const dry = settings.data?.config.dry_run ?? true;
  const canSend = preview && !preview.errors.length && !preview.missing.length && !lead.suppressed;

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Email ${lead.full_name ?? lead.email}`}
      description="Review the exact rendered message before it is queued."
      size="lg"
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => previewMut.mutate()} loading={previewMut.isPending} disabled={!subject || !body || !accountId}>
            Preview
          </Button>
          <Button variant="primary" disabled={!canSend} loading={send.isPending} onClick={() => send.mutate()}>
            {dry ? "Simulate send" : "Confirm & send"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {lead.suppressed && <Alert tone="danger">This address is on the suppression list and cannot be emailed.</Alert>}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="From account" htmlFor="acc">
            <Select id="acc" value={accountId} onChange={(e) => setAccountId(Number(e.target.value))}>
              {accounts.data?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name} &lt;{a.email}&gt; — {a.status}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Start from" htmlFor="src">
            <Select
              id="src"
              value=""
              onChange={(e) => {
                const [kind, id] = e.target.value.split(":");
                if (kind === "t") {
                  const t = templates.data?.find((x) => x.id === Number(id));
                  if (t?.current) {
                    setSubject(t.current.subject);
                    setBody(t.current.body);
                    setDraftId(null);
                  }
                } else if (kind === "d") {
                  const d = drafts.data?.find((x) => x.id === Number(id));
                  if (d) {
                    setSubject(d.subject);
                    setBody(d.body);
                    setDraftId(d.id);
                  }
                }
              }}
            >
              <option value="">Template or approved AI draft…</option>
              <optgroup label="Templates">
                {templates.data?.map((t) => (
                  <option key={t.id} value={`t:${t.id}`}>
                    {t.name}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Approved AI drafts">
                {drafts.data?.map((d) => (
                  <option key={d.id} value={`d:${d.id}`}>
                    #{d.id} {d.subject}
                  </option>
                ))}
              </optgroup>
            </Select>
          </Field>
        </div>
        <Field label="Subject" htmlFor="subj">
          <Input
            id="subj"
            value={subject}
            onChange={(e) => {
              setSubject(e.target.value);
              setDraftId(null);
            }}
          />
        </Field>
        <Field label="Body" htmlFor="body" hint="Variables like {{first_name}} are filled from the lead.">
          <Textarea
            id="body"
            rows={8}
            value={body}
            onChange={(e) => {
              setBody(e.target.value);
              setDraftId(null);
            }}
          />
        </Field>
        {preview && (
          <div className="rounded-lg border border-border bg-bg/50 p-3">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">Final message to {lead.email}</p>
            {preview.errors.length > 0 && <Alert tone="danger">{preview.errors.join("; ")}</Alert>}
            {preview.missing.length > 0 && <Alert tone="danger">Missing values for: {preview.missing.join(", ")}. Fill them in on the lead or add a fallback like {"{{first_name|there}}"}.</Alert>}
            {preview.subject && <p className="text-sm font-semibold">{preview.subject}</p>}
            {preview.text && <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-ink-2">{preview.text}</pre>}
          </div>
        )}
      </div>
    </Dialog>
  );
}
