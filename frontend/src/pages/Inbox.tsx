import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCheck, Inbox as InboxIcon, Link2, Reply, Search, StopCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { LeadPicker } from "@/components/LeadPicker";
import { Alert, Badge, Button, Card, EmptyState, Field, Input, Select, Spinner, StatusBadge, Textarea } from "@/components/ui";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api";
import { cn, fmtDate, fmtRelative, titleCase } from "@/lib/utils";
import type { ConversationDetail, ConversationSummary, Lead } from "@/types/api";

const LABELS = ["interested", "not_interested", "referral", "question", "out_of_office", "unsubscribe", "automated", "unknown"];

export function InboxPage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("c") ? Number(params.get("c")) : null;
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState({ needs_review: "", status: "open", classification: "" });
  const list = useQuery({
    queryKey: ["inbox", filters, q],
    queryFn: () => api.get<{ items: ConversationSummary[]; total: number; counts: Record<string, number> }>("/api/inbox", { ...filters, q, page_size: 50 }),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });

  return (
    <>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-[22px]">Inbox</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">Replies matched to your outreach. Classifications are suggestions — review and correct them. Nothing is ever replied to automatically.</p>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[380px_1fr]">
        <Card className={cn("flex min-h-[60vh] flex-col", selected && "hidden lg:flex")}>
          <div className="space-y-2 border-b border-border p-3">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
              <Input aria-label="Search inbox" className="pl-9" placeholder="Search subject, name, email" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <Select aria-label="Status" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
                <option value="">All</option>
                <option value="open">Open ({list.data?.counts.open ?? 0})</option>
                <option value="handled">Handled ({list.data?.counts.handled ?? 0})</option>
              </Select>
              <Select aria-label="Review" value={filters.needs_review} onChange={(e) => setFilters({ ...filters, needs_review: e.target.value })}>
                <option value="">Any</option>
                <option value="true">Needs review</option>
                <option value="false">Reviewed</option>
              </Select>
              <Select aria-label="Classification" value={filters.classification} onChange={(e) => setFilters({ ...filters, classification: e.target.value })}>
                <option value="">Any label</option>
                {LABELS.map((l) => (
                  <option key={l} value={l}>
                    {titleCase(l)}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          {list.isLoading ? (
            <Spinner />
          ) : !list.data?.items.length ? (
            <EmptyState icon={<InboxIcon className="h-6 w-6" />} title="No conversations" description="Replies appear after the worker syncs an IMAP-enabled sending account." />
          ) : (
            <ul className="scroll-thin flex-1 divide-y divide-border overflow-y-auto">
              {list.data.items.map((c) => (
                <li key={c.id}>
                  <button onClick={() => setParams({ c: String(c.id) })} className={cn("w-full px-4 py-3 text-left hover:bg-bg", selected === c.id && "bg-brand-soft/60")}>
                    <div className="flex items-center justify-between gap-2">
                      <span className={cn("truncate text-sm", c.needs_review ? "font-semibold text-ink" : "text-ink-2")}>{c.lead?.name ?? c.lead?.email ?? c.from_email}</span>
                      <span className="shrink-0 text-xs text-muted">{fmtRelative(c.last_message_at)}</span>
                    </div>
                    <p className="truncate text-[13px] text-ink-2">{c.subject}</p>
                    <p className="truncate text-xs text-muted">{c.snippet}</p>
                    <div className="mt-1.5 flex items-center gap-1.5">
                      <StatusBadge status={c.classification} />
                      {c.needs_review && <Badge tone="warn">review</Badge>}
                      {c.campaign && <span className="truncate text-[11px] text-faint">{c.campaign.name}</span>}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {selected ? (
          <ConversationView id={selected} onBack={() => setParams({})} />
        ) : (
          <Card className="hidden lg:block">
            <EmptyState title="Select a conversation" />
          </Card>
        )}
      </div>
    </>
  );
}

function ConversationView({ id, onBack }: { id: number; onBack: () => void }) {
  const qc = useQueryClient();
  const conv = useQuery({ queryKey: ["conversation", id], queryFn: () => api.get<ConversationDetail>(`/api/inbox/${id}`) });
  const [notes, setNotes] = useState("");
  const [labels, setLabels] = useState("");
  const [confirm, setConfirm] = useState<"suppress" | "stop" | null>(null);
  const [reassignOpen, setReassignOpen] = useState(false);
  const [replyOpen, setReplyOpen] = useState(false);

  useEffect(() => {
    if (conv.data) {
      setNotes(conv.data.notes ?? "");
      setLabels(conv.data.labels.join(", "));
    }
  }, [conv.data]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["conversation", id] });
    qc.invalidateQueries({ queryKey: ["inbox"] });
    qc.invalidateQueries({ queryKey: ["inbox-count"] });
  };
  const mut = useMutation({
    mutationFn: ({ path, body, method = "post" }: { path: string; body?: unknown; method?: "post" | "patch" }) => (method === "patch" ? api.patch(`/api/inbox/${id}${path}`, body) : api.post(`/api/inbox/${id}${path}`, body)),
    onSuccess: () => {
      toast.success("Saved");
      setConfirm(null);
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (conv.isLoading) return <Card><Spinner /></Card>;
  if (!conv.data) return <Card><EmptyState title="Conversation not found" /></Card>;
  const c = conv.data;
  const lastInbound = [...c.messages].reverse().find((m) => m.direction === "inbound");

  return (
    <Card className="flex min-h-[60vh] flex-col">
      <div className="border-b border-border p-4">
        <button className="mb-2 text-sm text-brand lg:hidden" onClick={onBack}>
          ← Back
        </button>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold">{c.subject ?? "(no subject)"}</h2>
            <p className="text-sm text-muted">
              {c.lead ? (
                <Link className="hover:text-brand" to={`/leads/${c.lead.id}`}>
                  {c.lead.name ?? c.lead.email} · {c.lead.email}
                </Link>
              ) : (
                "Unmatched sender"
              )}
              {c.campaign && (
                <>
                  {" "}·{" "}
                  <Link className="hover:text-brand" to={`/campaigns/${c.campaign.id}`}>
                    {c.campaign.name}
                  </Link>
                </>
              )}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setReplyOpen(true)} disabled={!c.lead || c.suppressed}>
              <Reply className="h-3.5 w-3.5" /> Reply
            </Button>
            <Button size="sm" onClick={() => mut.mutate({ path: "", method: "patch", body: { status: c.status === "handled" ? "open" : "handled" } })}>
              <CheckCheck className="h-3.5 w-3.5" /> {c.status === "handled" ? "Reopen" : "Mark handled"}
            </Button>
            <Button size="sm" onClick={() => setConfirm("stop")} disabled={!c.lead}>
              <StopCircle className="h-3.5 w-3.5" /> Stop follow-ups
            </Button>
            <Button size="sm" onClick={() => setConfirm("suppress")} disabled={!c.lead || c.suppressed}>
              <Ban className="h-3.5 w-3.5" /> Suppress
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setReassignOpen(true)}>
              <Link2 className="h-3.5 w-3.5" /> Re-match
            </Button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs text-muted">Classification{c.classification_source ? ` (${c.classification_source})` : ""}:</span>
          {LABELS.map((l) => (
            <button
              key={l}
              onClick={() => mut.mutate({ path: "/classify", body: { label: l } })}
              className={cn("rounded-full px-2.5 py-0.5 text-xs ring-1", c.classification === l ? "bg-brand text-white ring-brand" : "bg-surface text-ink-2 ring-border hover:ring-brand")}
            >
              {titleCase(l)}
            </button>
          ))}
        </div>
        {c.suppressed && <Alert tone="danger" className="mt-3">This contact is suppressed; no further outreach will be sent.</Alert>}
      </div>
      <div className="scroll-thin flex-1 space-y-3 overflow-y-auto bg-bg/40 p-4">
        {c.messages.map((m) => (
          <article key={m.id} className={cn("rounded-xl border p-4", m.direction === "inbound" ? "border-border bg-surface" : "ml-6 border-brand/15 bg-brand-soft/40")}>
            <header className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
              <span>
                <span className="font-medium text-ink">{m.direction === "inbound" ? m.from_name ?? m.from_email : "You"}</span> {m.direction === "inbound" ? `<${m.from_email}>` : `→ ${m.to_email}`}
                {m.is_simulation && <Badge tone="info" className="ml-2">simulated — not sent</Badge>}
                {m.is_auto_reply && <Badge className="ml-2">automated</Badge>}
                {m.direction === "inbound" && m.match_method && <span className="ml-2">matched by {m.match_method}</span>}
              </span>
              <span>{fmtDate(m.occurred_at)}</span>
            </header>
            <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-ink-2">{m.body_text ?? m.snippet ?? "(body removed by retention policy)"}</pre>
            {m.classifications[0] && m.direction === "inbound" && (
              <p className="mt-2 text-xs text-muted">
                Suggested: {titleCase(m.classifications[0].label)} ({Math.round(m.classifications[0].confidence * 100)}%) — {m.classifications[0].rationale}
              </p>
            )}
            <details className="mt-2 text-xs text-faint">
              <summary className="cursor-pointer">Original message details</summary>
              <p className="mt-1 break-all font-mono">Message-ID: {m.message_id_header}</p>
            </details>
          </article>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-3 border-t border-border p-4 md:grid-cols-[1fr_220px_auto]">
        <Field label="Notes" htmlFor="cnotes">
          <Textarea id="cnotes" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
        <Field label="Labels" htmlFor="clabels" hint="Comma separated">
          <Input id="clabels" value={labels} onChange={(e) => setLabels(e.target.value)} />
        </Field>
        <div className="flex items-end">
          <Button onClick={() => mut.mutate({ path: "", method: "patch", body: { notes, labels: labels.split(",").map((l) => l.trim()).filter(Boolean) } })}>Save</Button>
        </div>
      </div>

      <ConfirmDialog
        open={confirm === "suppress"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Add to suppression list?"
        description={`${c.lead?.email} will never be emailed again and all scheduled messages are cancelled.`}
        tone="danger"
        confirmLabel="Suppress"
        onConfirm={() => mut.mutate({ path: "/suppress", body: { reason: c.classification === "unsubscribe" ? "unsubscribed" : "do_not_contact" } })}
        loading={mut.isPending}
      />
      <ConfirmDialog
        open={confirm === "stop"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Stop follow-ups?"
        description="All active sequences for this lead stop. The lead is not suppressed."
        confirmLabel="Stop follow-ups"
        onConfirm={() => mut.mutate({ path: "/stop-followups" })}
        loading={mut.isPending}
      />
      {reassignOpen && <ReassignDialog conv={c} onClose={() => { setReassignOpen(false); refresh(); }} />}
      {replyOpen && <ReplyDialog conv={c} inReplyToSubject={lastInbound?.subject ?? c.subject} onClose={() => { setReplyOpen(false); refresh(); }} />}
    </Card>
  );
}

function ReassignDialog({ conv, onClose }: { conv: ConversationDetail; onClose: () => void }) {
  const [lead, setLead] = useState<Lead | null>(null);
  const [campaignId, setCampaignId] = useState<string>("");
  const save = useMutation({
    mutationFn: () => api.post(`/api/inbox/${conv.id}/reassign`, { lead_id: lead?.id ?? null, campaign_id: campaignId ? Number(campaignId) : null }),
    onSuccess: () => {
      toast.success("Conversation re-matched");
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} title="Correct the match" description="Attach this conversation to the right lead (and optionally campaign)." size="sm"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!lead} loading={save.isPending} onClick={() => save.mutate()}>Save</Button></>}>
      <div className="space-y-3">
        <LeadPicker value={lead} onChange={(l) => { setLead(l); setCampaignId(""); }} label="Lead" />
        {lead && lead.campaigns.length > 0 && (
          <Field label="Campaign" htmlFor="rc">
            <Select id="rc" value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
              <option value="">None</option>
              {lead.campaigns.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </div>
    </Dialog>
  );
}

function ReplyDialog({ conv, inReplyToSubject, onClose }: { conv: ConversationDetail; inReplyToSubject: string | null; onClose: () => void }) {
  const [body, setBody] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const [key] = useState(newIdempotencyKey);
  const subject = inReplyToSubject?.toLowerCase().startsWith("re:") ? inReplyToSubject : `Re: ${inReplyToSubject ?? ""}`;
  const send = useMutation({
    mutationFn: () => api.post<{ simulation: boolean }>(`/api/inbox/${conv.id}/reply`, { body, confirm: true, idempotency_key: key }),
    onSuccess: (r) => {
      toast.success(r.simulation ? "Reply queued as a dry-run simulation (not sent)" : "Reply queued for sending");
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} title={reviewing ? "Confirm reply" : "Reply"} size="lg"
      footer={
        reviewing ? (
          <>
            <Button onClick={() => setReviewing(false)}>Edit</Button>
            <Button variant="primary" loading={send.isPending} onClick={() => send.mutate()}>Send this reply</Button>
          </>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" disabled={!body.trim() || body.includes("{{")} onClick={() => setReviewing(true)}>Review</Button>
          </>
        )
      }>
      {reviewing ? (
        <div className="rounded-lg border border-border p-4">
          <p className="text-xs text-muted">To: {conv.lead?.email}</p>
          <p className="mt-1 text-sm font-semibold">{subject}</p>
          <pre className="mt-3 whitespace-pre-wrap font-sans text-sm">{body}</pre>
        </div>
      ) : (
        <Field label="Message" htmlFor="rb" hint="Sent verbatim as plain text, threaded with the conversation.">
          <Textarea id="rb" rows={10} value={body} onChange={(e) => setBody(e.target.value)} autoFocus />
        </Field>
      )}
    </Dialog>
  );
}
