import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Plus, Search } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, Checkbox, EmptyState, ErrorState, Field, Input, Pagination, Select, Spinner, StatusBadge, Table, Td, Textarea, Th } from "@/components/ui";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { fmtDate, titleCase } from "@/lib/utils";
import type { DiscoverySignal, DiscoverySignalDetail, Page } from "@/types/api";

const STATES = ["discovered", "fetch_pending", "fetched", "qualified", "review_required", "draft_pending", "drafted", "rejected", "failed", "archived"];

export function safeHref(url: string | null | undefined): string | undefined {
  return url && /^https?:\/\//i.test(url) ? url : undefined;
}

export function DiscoverySignals({ categories }: { categories: string[] }) {
  const [filters, setFilters] = useState({ q: "", processing_status: "", provider: "", category: "" });
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const list = useQuery({
    queryKey: ["discovery", "signals", filters, page],
    queryFn: () => api.get<Page<DiscoverySignal>>("/api/discovery/signals", { ...filters, page, page_size: 25 }),
    placeholderData: keepPreviousData,
  });
  const set = (k: keyof typeof filters, v: string) => {
    setFilters({ ...filters, [k]: v });
    setPage(1);
  };

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
          <Input aria-label="Search signals" className="pl-9" placeholder="Search title or company" value={filters.q} onChange={(e) => set("q", e.target.value)} />
        </div>
        <Select aria-label="Processing state" className="w-auto" value={filters.processing_status} onChange={(e) => set("processing_status", e.target.value)}>
          <option value="">Active signals</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {titleCase(s)}
            </option>
          ))}
        </Select>
        <Select aria-label="Source" className="w-auto" value={filters.provider} onChange={(e) => set("provider", e.target.value)}>
          <option value="">All sources</option>
          {["rss", "website", "apollo", "google_cse", "manual"].map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </Select>
        <Select aria-label="Category" className="w-auto" value={filters.category} onChange={(e) => set("category", e.target.value)}>
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {titleCase(c)}
            </option>
          ))}
        </Select>
        <Button variant="primary" onClick={() => setSubmitting(true)}>
          <Plus className="h-4 w-4" /> Add signal
        </Button>
      </div>
      {list.isLoading ? (
        <Spinner />
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : !list.data?.items.length ? (
        <EmptyState title="No signals" description="Signals appear after a discovery run, or add one yourself (for example a LinkedIn post you're allowed to use)." />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>Signal</Th>
                <Th>Company / contact</Th>
                <Th>Category</Th>
                <Th>Source</Th>
                <Th>Published / found</Th>
                <Th className="text-right">Score</Th>
                <Th>State</Th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((s) => (
                <tr key={s.id} className="cursor-pointer hover:bg-bg/60" onClick={() => setOpenId(s.id)}>
                  <Td className="max-w-[320px]">
                    <p className="truncate font-medium text-ink">{s.title ?? "(untitled)"}</p>
                    <p className="truncate text-xs text-muted">{s.excerpt}</p>
                  </Td>
                  <Td>
                    <p>{s.company_name ?? s.company_domain ?? "—"}</p>
                    {s.contact_name && <p className="text-xs text-muted">{s.contact_name}{s.contact_title ? ` · ${s.contact_title}` : ""}</p>}
                  </Td>
                  <Td>{s.category ? <Badge>{titleCase(s.category)}</Badge> : "—"}</Td>
                  <Td className="text-xs">
                    {s.source_provider}
                    {s.test_mode && <Badge tone="info" className="ml-1">test</Badge>}
                  </Td>
                  <Td className="whitespace-nowrap text-xs">
                    {fmtDate(s.published_at, false)}
                    <br />
                    <span className="text-muted">{fmtDate(s.discovered_at)}</span>
                  </Td>
                  <Td className="tabular text-right">{s.relevance_score ?? "—"}</Td>
                  <Td>
                    <StatusBadge status={s.processing_status === "drafted" ? "active" : s.processing_status === "review_required" ? "needs_review" : s.processing_status === "rejected" ? "stopped" : s.processing_status} label={titleCase(s.processing_status)} />
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <Pagination page={page} pageSize={25} total={list.data.total} onPage={setPage} />
        </>
      )}
      {openId !== null && <SignalDetail id={openId} onClose={() => setOpenId(null)} />}
      {submitting && <ManualSignalDialog categories={categories} onClose={() => setSubmitting(false)} />}
    </Card>
  );
}

function SignalDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<"archive" | "purge" | null>(null);
  const d = useQuery({ queryKey: ["discovery", "signal", id], queryFn: () => api.get<DiscoverySignalDetail>(`/api/discovery/signals/${id}`) });
  const act = useMutation({
    mutationFn: (action: "reprocess" | "archive" | "purge-evidence") => api.post(`/api/discovery/signals/${id}/${action}`),
    onSuccess: (_r, action) => {
      toast.success(action === "reprocess" ? "Queued for reprocessing — a new draft version will be created if it qualifies" : action === "archive" ? "Signal archived" : "Evidence deleted");
      qc.invalidateQueries({ queryKey: ["discovery"] });
      setConfirm(null);
      if (action !== "reprocess") onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const s = d.data;
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} title={s?.title ?? "Signal"} size="xl"
      footer={
        s && (
          <>
            <Button variant="ghost" onClick={() => setConfirm("purge")}>Delete evidence</Button>
            <Button onClick={() => setConfirm("archive")}>Archive</Button>
            <Button variant="primary" loading={act.isPending} onClick={() => act.mutate("reprocess")} disabled={s.processing_status === "archived"}>
              Reprocess
            </Button>
          </>
        )
      }>
      {!s ? (
        <Spinner />
      ) : (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={s.qualification_status === "qualified" ? "active" : s.qualification_status === "rejected" ? "stopped" : "needs_review"} label={titleCase(s.qualification_status)} />
            <Badge>{titleCase(s.processing_status)}</Badge>
            {s.category && <Badge tone="brand">{titleCase(s.category)}</Badge>}
            <span className="text-muted">
              {s.source_provider} · {s.source_type} · score {s.relevance_score ?? "—"}
            </span>
            {safeHref(s.source_url) && (
              <a href={safeHref(s.source_url)} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-brand hover:underline">
                Open original source <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
          {s.error_detail && <Alert tone="danger">{s.error_detail}</Alert>}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="rounded-lg border border-border p-3">
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">Company & contact</p>
              <p>{s.company_name ?? "—"} {s.company_domain && <span className="text-muted">({s.company_domain})</span>}</p>
              <p>{s.contact_name ?? "No named contact"} {s.contact_title && <span className="text-muted">· {s.contact_title}</span>}</p>
              {s.lead ? (
                <Link className="text-brand hover:underline" to={`/leads/${s.lead.id}`}>Lead: {s.lead.email}</Link>
              ) : (
                <p className="text-muted">No lead (no verified contact email)</p>
              )}
              <p className="mt-1 text-xs text-muted">Published {fmtDate(s.published_at)} · found {fmtDate(s.discovered_at)}</p>
            </div>
            <div className="rounded-lg border border-border p-3">
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">Why this score</p>
              <ul className="space-y-1">
                {s.reasons.map((r, i) => (
                  <li key={i} className={r.type === "exclusion" || r.type.startsWith("draft_warning") ? "text-danger" : ""}>
                    <span className="font-mono text-[11px] text-muted">{r.type}</span> {r.reason}
                    {typeof r.score === "number" && <span className="text-xs text-muted"> ({Math.round(r.score * 100)}%)</span>}
                  </li>
                ))}
                {!s.reasons.length && <li className="text-muted">Not processed yet</li>}
              </ul>
            </div>
          </div>
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">Evidence (third-party text, shown as data)</p>
            <ul className="space-y-2">
              {s.evidence.map((e, i) => (
                <li key={i} className="rounded-lg border border-border bg-bg/40 p-3">
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                    <Badge>{e.kind}</Badge> {e.provider} · retrieved {fmtDate(e.retrieved_at)}
                    {safeHref(e.url) && (
                      <a href={safeHref(e.url)} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-brand hover:underline">
                        source <ExternalLink className="h-3 w-3" />
                      </a>
                    )}
                  </div>
                  <p className="whitespace-pre-wrap text-ink-2">{e.text || e.error || "(no text)"}</p>
                </li>
              ))}
              {!s.evidence.length && <li className="text-muted">No evidence stored (deleted by retention or by you).</li>}
            </ul>
          </div>
          {!!s.drafts.length && (
            <div>
              <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">Drafts</p>
              <ul className="space-y-1">
                {s.drafts.map((dr) => (
                  <li key={dr.id}>
                    #{dr.id} {dr.label} — {dr.subject} <StatusBadge status={dr.status === "approved" ? "approved" : "draft"} label={dr.status} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      <ConfirmDialog open={confirm === "archive"} onOpenChange={(v) => !v && setConfirm(null)} title="Archive this signal?" description="It disappears from the active list. Drafts are kept." confirmLabel="Archive" loading={act.isPending} onConfirm={() => act.mutate("archive")} />
      <ConfirmDialog open={confirm === "purge"} onOpenChange={(v) => !v && setConfirm(null)} title="Delete stored evidence?" description="The excerpt and evidence text are erased and the signal is archived. This can't be undone." tone="danger" confirmLabel="Delete evidence" loading={act.isPending} onConfirm={() => act.mutate("purge-evidence")} />
    </Dialog>
  );
}

function ManualSignalDialog({ categories, onClose }: { categories: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [v, setV] = useState({ url: "", text: "", title: "", company_name: "", company_domain: "", contact_name: "", contact_title: "", contact_email: "", category: "", authorised: false });
  const set = (p: Partial<typeof v>) => setV({ ...v, ...p });
  const isLinkedIn = /linkedin\.com\//i.test(v.url);
  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { authorised: v.authorised };
      for (const [k, val] of Object.entries(v)) if (k !== "authorised" && typeof val === "string" && val.trim()) body[k] = val.trim();
      return api.post<DiscoverySignal>("/api/discovery/signals/manual", body);
    },
    onSuccess: () => {
      toast.success("Signal added — the worker will qualify it and draft an email if it fits");
      qc.invalidateQueries({ queryKey: ["discovery"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const urlOk = !v.url || /^https?:\/\/\S+\.\S+/i.test(v.url);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Add a signal" description="Paste a public post, job listing or announcement you're allowed to use. LinkedIn posts are never fetched — paste the text." size="lg"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!v.authorised || !urlOk || (!v.text.trim() && (!v.url || isLinkedIn))} loading={save.isPending} onClick={() => save.mutate()}>Add signal</Button></>}>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Source URL" htmlFor="m-url" error={urlOk ? undefined : "Must start with http:// or https://"} className="sm:col-span-2">
          <Input id="m-url" value={v.url} onChange={(e) => set({ url: e.target.value })} placeholder="https://…" />
        </Field>
        <Field label={isLinkedIn ? "Post text (required for LinkedIn)" : "Text (optional if the URL is a public page)"} htmlFor="m-text" className="sm:col-span-2">
          <Textarea id="m-text" rows={5} value={v.text} onChange={(e) => set({ text: e.target.value })} />
        </Field>
        <Field label="Title" htmlFor="m-title"><Input id="m-title" value={v.title} onChange={(e) => set({ title: e.target.value })} /></Field>
        <Field label="Category" htmlFor="m-cat">
          <Select id="m-cat" value={v.category} onChange={(e) => set({ category: e.target.value })}>
            <option value="">Detect automatically</option>
            {categories.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
          </Select>
        </Field>
        <Field label="Company" htmlFor="m-co"><Input id="m-co" value={v.company_name} onChange={(e) => set({ company_name: e.target.value })} /></Field>
        <Field label="Company domain" htmlFor="m-dom"><Input id="m-dom" value={v.company_domain} onChange={(e) => set({ company_domain: e.target.value })} placeholder="example.com" /></Field>
        <Field label="Contact name" htmlFor="m-cn"><Input id="m-cn" value={v.contact_name} onChange={(e) => set({ contact_name: e.target.value })} /></Field>
        <Field label="Contact title" htmlFor="m-ct"><Input id="m-ct" value={v.contact_title} onChange={(e) => set({ contact_title: e.target.value })} /></Field>
        <Field label="Contact email (only if you know it's correct)" htmlFor="m-ce" hint="Needed for a draft. Never guessed." className="sm:col-span-2">
          <Input id="m-ce" type="email" value={v.contact_email} onChange={(e) => set({ contact_email: e.target.value })} />
        </Field>
        <label className="flex items-start gap-2 text-sm sm:col-span-2">
          <Checkbox checked={v.authorised} onChange={(e) => set({ authorised: e.target.checked })} className="mt-0.5" />
          <span>I'm authorised to use this content and contact details for business outreach.</span>
        </label>
      </div>
    </Dialog>
  );
}
