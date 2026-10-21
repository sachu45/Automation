import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Copy, Pause, Pencil, Play, Rocket, Send, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Field, Input, KeyValue, PageHeader, Pagination, Select, Spinner, StatCard, StatusBadge, Table, Tabs, Td, Th } from "@/components/ui";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { fmtDate, titleCase, WEEKDAYS } from "@/lib/utils";
import type { Campaign, CampaignReview, Page, Recipient } from "@/types/api";

type Action = "submit" | "approve" | "activate" | "pause" | "resume" | "cancel" | "delete" | "return-to-draft" | "duplicate";

export function CampaignDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [tab, setTab] = useState<"review" | "recipients" | "attempts">("review");
  const [confirm, setConfirm] = useState<Action | null>(null);
  const [opsOpen, setOpsOpen] = useState(false);
  const campaign = useQuery({ queryKey: ["campaign", id], queryFn: () => api.get<Campaign>(`/api/campaigns/${id}`), refetchInterval: 15_000 });
  const c = campaign.data;
  const preStart = c && ["draft", "ready_for_review", "approved"].includes(c.status);
  const review = useQuery({ queryKey: ["campaign", id, "review"], queryFn: () => api.get<CampaignReview>(`/api/campaigns/${id}/review`), enabled: !!preStart });

  const act = useMutation({
    mutationFn: ({ action, body }: { action: Action; body?: unknown }) => {
      if (action === "delete") return api.delete(`/api/campaigns/${id}`);
      return api.post<Campaign>(`/api/campaigns/${id}/${action}`, body);
    },
    onSuccess: (data, { action }) => {
      setConfirm(null);
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      if (action === "delete") {
        toast.success("Campaign deleted");
        return navigate("/campaigns");
      }
      if (action === "duplicate") {
        toast.success("Copied as a new draft");
        return navigate(`/campaigns/${(data as Campaign).id}`);
      }
      toast.success(
        {
          submit: "Submitted for review",
          approve: "Approved — recipients and content are frozen",
          activate: "Activated — the worker will send within the window and limits",
          pause: "Paused",
          resume: "Resumed",
          cancel: "Cancelled; all scheduled messages stopped",
          "return-to-draft": "Returned to draft",
        }[action] ?? "Done",
      );
      qc.invalidateQueries({ queryKey: ["campaign", id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (campaign.isLoading) return <Spinner />;
  if (campaign.isError || !c) return <ErrorState error={campaign.error} />;
  const rc = c.recipient_counts;
  const total = Object.values(rc).reduce((a, b) => a + b, 0);

  return (
    <>
      <Link to="/campaigns" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
        <ArrowLeft className="h-4 w-4" /> Campaigns
      </Link>
      <PageHeader
        title={c.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={c.status} />
            {!preStart && <Badge tone={c.dry_run ? "info" : "ok"}>{c.dry_run ? "dry run — simulated" : "live"}</Badge>}
            {c.description && <span>{c.description}</span>}
          </span>
        }
        actions={
          <>
            {c.status === "draft" && (
              <>
                <Button variant="ghost" onClick={() => setConfirm("delete")}>
                  <Trash2 className="h-4 w-4" /> Delete
                </Button>
                <Link to={`/campaigns/${c.id}/edit`}>
                  <Button>
                    <Pencil className="h-4 w-4" /> Edit
                  </Button>
                </Link>
                <Button variant="primary" onClick={() => act.mutate({ action: "submit" })} loading={act.isPending}>
                  <Send className="h-4 w-4" /> Submit for review
                </Button>
              </>
            )}
            {c.status === "ready_for_review" && (
              <>
                <Button onClick={() => act.mutate({ action: "return-to-draft" })}>Return to draft</Button>
                <Button variant="primary" disabled={!!review.data?.problems.length} onClick={() => setConfirm("approve")}>
                  <CheckCircle2 className="h-4 w-4" /> Approve
                </Button>
              </>
            )}
            {c.status === "approved" && (
              <>
                <Button onClick={() => act.mutate({ action: "return-to-draft" })}>Return to draft</Button>
                <Button variant="primary" onClick={() => setConfirm("activate")}>
                  <Rocket className="h-4 w-4" /> Activate
                </Button>
              </>
            )}
            {["scheduled", "active"].includes(c.status) && (
              <Button onClick={() => act.mutate({ action: "pause" })} loading={act.isPending}>
                <Pause className="h-4 w-4" /> Pause
              </Button>
            )}
            {c.status === "paused" && (
              <Button variant="primary" onClick={() => act.mutate({ action: "resume" })} loading={act.isPending}>
                <Play className="h-4 w-4" /> Resume
              </Button>
            )}
            {["scheduled", "active", "paused"].includes(c.status) && (
              <>
                <Button onClick={() => setOpsOpen(true)}>Adjust limits</Button>
                <Button variant="danger" onClick={() => setConfirm("cancel")}>
                  <XCircle className="h-4 w-4" /> Cancel
                </Button>
              </>
            )}
            <Button variant="ghost" onClick={() => act.mutate({ action: "duplicate" })}>
              <Copy className="h-4 w-4" /> Duplicate
            </Button>
          </>
        }
      />

      {!preStart && (
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <StatCard label="Recipients" value={total} />
          <StatCard label="Active" value={rc.active ?? 0} />
          <StatCard label="Replied" value={rc.replied ?? 0} tone="ok" />
          <StatCard label="Completed" value={rc.completed ?? 0} />
          <StatCard label="Bounced / failed" value={(rc.bounced ?? 0) + (rc.failed ?? 0)} tone={(rc.bounced ?? 0) + (rc.failed ?? 0) ? "danger" : undefined} />
          <StatCard label="Stopped / suppressed" value={(rc.stopped ?? 0) + (rc.suppressed ?? 0) + (rc.unsubscribed ?? 0)} hint={rc.needs_review ? `${rc.needs_review} need review` : undefined} />
        </div>
      )}
      {!!rc.needs_review && (
        <Alert tone="warn" className="mb-5" title={`${rc.needs_review} recipient(s) need review`}>
          The worker stopped while sending, so it's unknown whether the email went out. They are never resent automatically — resolve them in the Recipients tab.
        </Alert>
      )}

      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "review", label: preStart ? "Review" : "Overview" },
          { value: "recipients", label: `Recipients${total ? ` (${total})` : ""}` },
          { value: "attempts", label: "Send log" },
        ]}
      />
      {tab === "review" && (preStart ? <ReviewPanel review={review} campaign={c} /> : <Overview campaign={c} />)}
      {tab === "recipients" && <RecipientsPanel campaignId={c.id} />}
      {tab === "attempts" && <AttemptsPanel campaignId={c.id} />}

      <ConfirmDialog
        open={confirm === "approve"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Approve this campaign?"
        description={`${review.data?.eligible ?? 0} eligible recipient(s) and the current template versions will be frozen. You'll activate it in a separate step.`}
        confirmLabel="Approve"
        loading={act.isPending}
        onConfirm={() => act.mutate({ action: "approve", body: { confirm: true } })}
      />
      <ConfirmDialog
        open={confirm === "activate"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Activate campaign"
        description={review.data?.mode === "live" ? "LIVE MODE: real emails will be sent to the approved recipients within the sending window and limits." : "Dry-run mode: messages will be rendered and recorded as simulations. Nothing is sent."}
        confirmLabel={review.data?.mode === "live" ? "Activate & send" : "Activate dry run"}
        tone={review.data?.mode === "live" ? "danger" : "primary"}
        typeToConfirm={c.name}
        loading={act.isPending}
        onConfirm={(typed) => act.mutate({ action: "activate", body: { confirm: true, confirm_name: typed } })}
      >
        <KeyValue
          items={[
            ["Recipients", c.recipient_counts.pending ?? 0],
            ["Window", `${c.window_start}–${c.window_end} ${c.timezone}`],
            ["Daily limit", c.daily_limit],
          ]}
        />
      </ConfirmDialog>
      <ConfirmDialog
        open={confirm === "cancel"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Cancel campaign?"
        description="All queued and scheduled messages are stopped. This cannot be undone; duplicate the campaign to run it again."
        confirmLabel="Cancel campaign"
        tone="danger"
        loading={act.isPending}
        onConfirm={() => act.mutate({ action: "cancel", body: { confirm: true } })}
      />
      <ConfirmDialog
        open={confirm === "delete"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Delete draft campaign?"
        tone="danger"
        confirmLabel="Delete"
        loading={act.isPending}
        onConfirm={() => act.mutate({ action: "delete" })}
      />
      {opsOpen && <OperationalDialog campaign={c} onClose={() => setOpsOpen(false)} />}
    </>
  );
}

function ReviewPanel({ review, campaign }: { review: ReturnType<typeof useQuery<CampaignReview>>; campaign: Campaign }) {
  if (review.isLoading) return <Spinner label="Checking recipients" />;
  if (review.isError || !review.data) return <ErrorState error={review.error} onRetry={() => review.refetch()} />;
  const r = review.data;
  const ex = r.excluded;
  return (
    <div className="space-y-6">
      {r.problems.length > 0 && (
        <Alert tone="danger" title="Fix before approval">
          <ul className="list-disc pl-4">
            {r.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </Alert>
      )}
      {r.warnings.map((w) => (
        <Alert key={w} tone="warn">
          {w}
        </Alert>
      ))}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <StatCard label="Eligible recipients" value={r.eligible} tone="ok" />
        <StatCard label="Candidates" value={r.candidates} hint={`${r.duplicates_across_sources} duplicates merged`} />
        <StatCard label="Invalid emails" value={ex.invalid_email} />
        <StatCard label="Suppressed" value={ex.suppressed} tone={ex.suppressed ? "danger" : undefined} />
        <StatCard label="Not contactable" value={ex.blocked_status} hint="bounced / unsubscribed / DNC" />
        <StatCard label="Missing fields" value={ex.missing_fields} tone={ex.missing_fields ? "danger" : undefined} />
        <StatCard label="Other exclusions" value={ex.excluded_domain + ex.recently_contacted + ex.in_other_active_campaign} hint="domain / recent / other campaign" />
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Sequence" description={campaign.status === "approved" ? "Frozen at approval" : "Current template versions (frozen at approval)"} />
          <ol className="divide-y divide-border">
            {r.steps.map((s) => (
              <li key={s.step_number} className="px-5 py-3 text-sm">
                <div className="flex items-center gap-2">
                  <Badge tone={s.step_number === 1 ? "brand" : "neutral"}>{s.step_number === 1 ? "Initial" : `Follow-up ${s.step_number - 1}`}</Badge>
                  {s.template_name && <span className="font-medium">{s.template_name}</span>}
                  <span className="text-xs text-muted">v{s.version}</span>
                  {s.step_number > 1 && (
                    <span className="ml-auto text-xs text-muted">
                      after {s.delay_days}d{s.delay_hours ? ` ${s.delay_hours}h` : ""}
                      {s.send_as_reply ? " · in thread" : ""}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-ink-2">{s.subject}</p>
              </li>
            ))}
          </ol>
        </Card>
        <Card>
          <CardHeader title="Sending" />
          <CardBody>
            <KeyValue
              items={[
                ["Mode", r.mode === "live" ? <Badge key="m" tone="ok">Live</Badge> : <Badge key="m" tone="info">Dry run (no email sent)</Badge>],
                ["Account", r.account ? <span key="a">{r.account.email} <StatusBadge status={r.account.status} />{!r.account.sending_enabled && <span className="ml-1 text-xs text-muted">(sending disabled)</span>}</span> : "—"],
                ["Window", `${r.window.start}–${r.window.end} ${r.window.timezone} · ${r.window.days.map((d) => WEEKDAYS[d]).join(", ")}`],
                ["Effective daily limit", `${r.limits.effective_daily}/day (campaign ${r.limits.campaign_daily}, account ${r.limits.account_daily}, global ${r.limits.global_daily})`],
                ["Hourly (account)", r.limits.account_hourly ?? "—"],
                ["Spacing", `≥ ${r.limits.min_delay_seconds}s between emails`],
                ["Max emails / recipient", r.limits.max_emails_per_recipient],
                ["Initial sends finish in", r.estimated_initial_send_days ? `~${r.estimated_initial_send_days} sending day(s)` : "—"],
              ]}
            />
          </CardBody>
        </Card>
      </div>
      {r.estimated_schedule.length > 0 && (
        <Card>
          <CardHeader title="Estimated schedule (initial emails)" description="Assumes no failures. Follow-ups are scheduled after each send, then rechecked before sending." />
          <CardBody className="flex flex-wrap gap-2">
            {r.estimated_schedule.slice(0, 30).map((d) => (
              <div key={d.date} className="rounded-lg bg-bg px-3 py-2 text-center ring-1 ring-border">
                <p className="text-xs text-muted">{d.date}</p>
                <p className="tabular text-sm font-semibold">{d.emails}</p>
              </div>
            ))}
          </CardBody>
        </Card>
      )}
      {r.missing_fields.length > 0 && (
        <Card>
          <CardHeader title="Missing personalization" description="These leads are excluded. Fill the fields on the lead or add fallbacks like {{first_name|there}}." />
          <CardBody className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {r.missing_fields.map((m) => (
                <Badge key={m.variable} tone="warn">
                  {m.variable}: {m.count}
                </Badge>
              ))}
            </div>
            <ul className="text-sm">
              {r.missing_samples.map((s) => (
                <li key={s.lead_id}>
                  <Link className="text-brand hover:underline" to={`/leads/${s.lead_id}`}>
                    {s.email}
                  </Link>{" "}
                  <span className="text-muted">— {s.missing.join(", ")}</span>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

function Overview({ campaign: c }: { campaign: Campaign }) {
  const s = (c.approval_summary ?? {}) as Partial<CampaignReview>;
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Configuration" />
        <CardBody>
          <KeyValue
            items={[
              ["Window", `${c.window_start}–${c.window_end} ${c.timezone}`],
              ["Days", c.send_days.map((d) => WEEKDAYS[d]).join(", ")],
              ["Daily limit", c.daily_limit],
              ["Spacing", `${c.min_delay_seconds}s`],
              ["Max emails / recipient", c.max_emails_per_recipient],
              ["Start / end", `${fmtDate(c.start_at)} → ${fmtDate(c.end_at)}`],
              ["Approved", fmtDate(c.approved_at)],
              ["Activated", fmtDate(c.activated_at)],
              ["Completed / cancelled", fmtDate(c.completed_at ?? c.cancelled_at)],
            ]}
          />
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="At approval" />
        <CardBody>
          <KeyValue
            items={[
              ["Eligible", s.eligible ?? "—"],
              ["Candidates", s.candidates ?? "—"],
              ["Excluded", s.excluded ? Object.entries(s.excluded).filter(([, v]) => v).map(([k, v]) => `${titleCase(k)}: ${v}`).join(" · ") || "none" : "—"],
              ["Steps", s.steps?.map((st) => `v${st.version} ${st.subject}`).join(" → ") ?? "—"],
            ]}
          />
        </CardBody>
      </Card>
    </div>
  );
}

function RecipientsPanel({ campaignId }: { campaignId: number }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const recips = useQuery({ queryKey: ["recipients", campaignId, status, page], queryFn: () => api.get<Page<Recipient>>(`/api/campaigns/${campaignId}/recipients`, { status, page, page_size: 50 }) });
  const stop = useMutation({
    mutationFn: (rid: number) => api.post(`/api/campaigns/${campaignId}/recipients/${rid}/stop`),
    onSuccess: () => {
      toast.success("Recipient stopped");
      qc.invalidateQueries({ queryKey: ["recipients", campaignId] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const resolve = useMutation({
    mutationFn: ({ rid, was_sent }: { rid: number; was_sent: boolean }) => api.post(`/api/campaigns/${campaignId}/recipients/${rid}/resolve`, { was_sent }),
    onSuccess: () => {
      toast.success("Resolved");
      qc.invalidateQueries({ queryKey: ["recipients", campaignId] });
      qc.invalidateQueries({ queryKey: ["campaign"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <div className="flex justify-end border-b border-border p-3">
        <Select aria-label="Recipient status" className="w-auto" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All</option>
          {["pending", "active", "completed", "replied", "unsubscribed", "bounced", "suppressed", "failed", "stopped", "needs_review"].map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </Select>
      </div>
      {recips.isLoading ? (
        <Spinner />
      ) : !recips.data?.items.length ? (
        <EmptyState title="No recipients" description="Recipients are materialized when the campaign is approved." />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>Lead</Th>
                <Th>Status</Th>
                <Th>Sent</Th>
                <Th>Next send</Th>
                <Th>Last sent</Th>
                <Th>Reason</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {recips.data.items.map((r) => (
                <tr key={r.id}>
                  <Td>
                    <Link to={`/leads/${r.lead_id}`} className="font-medium text-ink hover:text-brand">
                      {r.name ?? r.email}
                    </Link>
                    <p className="text-xs text-muted">{r.email}</p>
                  </Td>
                  <Td>
                    <StatusBadge status={r.status} />
                  </Td>
                  <Td className="tabular">{r.steps_sent}</Td>
                  <Td className="whitespace-nowrap">{["active", "pending"].includes(r.status) ? fmtDate(r.next_send_at) : "—"}</Td>
                  <Td className="whitespace-nowrap">{fmtDate(r.last_sent_at)}</Td>
                  <Td className="text-xs">{r.stop_reason?.replace(/_/g, " ") ?? ""}</Td>
                  <Td className="whitespace-nowrap text-right">
                    {r.status === "needs_review" ? (
                      <>
                        <Button size="sm" onClick={() => resolve.mutate({ rid: r.id, was_sent: true })}>
                          It was sent
                        </Button>{" "}
                        <Button size="sm" onClick={() => resolve.mutate({ rid: r.id, was_sent: false })}>
                          Not sent — retry
                        </Button>
                      </>
                    ) : ["active", "pending"].includes(r.status) ? (
                      <Button size="sm" variant="ghost" onClick={() => stop.mutate(r.id)}>
                        Stop
                      </Button>
                    ) : null}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <Pagination page={page} pageSize={50} total={recips.data.total} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

function AttemptsPanel({ campaignId }: { campaignId: number }) {
  const attempts = useQuery({
    queryKey: ["attempts", campaignId],
    queryFn: () => api.get<{ counts: Record<string, number>; items: { id: number; to: string; step: number; status: string; simulation: boolean; attempt_no: number; smtp_code: number | null; error: string | null; subject: string | null; at: string }[] }>(`/api/campaigns/${campaignId}/attempts`),
    refetchInterval: 15_000,
  });
  if (attempts.isLoading) return <Spinner />;
  if (!attempts.data?.items.length) return <Card><EmptyState title="No send attempts yet" description="Attempts appear here as the worker processes the campaign." /></Card>;
  return (
    <Card>
      <div className="flex flex-wrap gap-2 border-b border-border p-3">
        {Object.entries(attempts.data.counts).map(([k, v]) => (
          <span key={k} className="flex items-center gap-1 text-sm">
            <StatusBadge status={k} /> <span className="tabular">{v}</span>
          </span>
        ))}
      </div>
      <Table>
        <thead>
          <tr>
            <Th>When</Th>
            <Th>To</Th>
            <Th>Step</Th>
            <Th>Status</Th>
            <Th>SMTP</Th>
            <Th>Detail</Th>
          </tr>
        </thead>
        <tbody>
          {attempts.data.items.map((a) => (
            <tr key={a.id}>
              <Td className="whitespace-nowrap">{fmtDate(a.at)}</Td>
              <Td>{a.to}</Td>
              <Td>
                {a.step}
                {a.attempt_no > 1 && <span className="text-xs text-muted"> (try {a.attempt_no})</span>}
              </Td>
              <Td>
                <StatusBadge status={a.status} />
              </Td>
              <Td className="tabular">{a.smtp_code ?? "—"}</Td>
              <Td className="max-w-[320px] truncate text-xs" title={a.error ?? undefined}>{a.error ?? a.subject}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

function OperationalDialog({ campaign, onClose }: { campaign: Campaign; onClose: () => void }) {
  const qc = useQueryClient();
  const [v, setV] = useState({ daily_limit: campaign.daily_limit, min_delay_seconds: campaign.min_delay_seconds, window_start: campaign.window_start, window_end: campaign.window_end });
  const save = useMutation({
    mutationFn: () => api.patch(`/api/campaigns/${campaign.id}/operational`, v),
    onSuccess: () => {
      toast.success("Updated");
      qc.invalidateQueries({ queryKey: ["campaign", String(campaign.id)] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Adjust limits & window" description="Content and recipients can't change after approval." size="sm"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save</Button></>}>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Daily limit" htmlFor="o1"><Input id="o1" type="number" min={1} value={v.daily_limit} onChange={(e) => setV({ ...v, daily_limit: Number(e.target.value) })} /></Field>
        <Field label="Spacing (s)" htmlFor="o2"><Input id="o2" type="number" min={10} value={v.min_delay_seconds} onChange={(e) => setV({ ...v, min_delay_seconds: Number(e.target.value) })} /></Field>
        <Field label="Window start" htmlFor="o3"><Input id="o3" type="time" value={v.window_start} onChange={(e) => setV({ ...v, window_start: e.target.value })} /></Field>
        <Field label="Window end" htmlFor="o4"><Input id="o4" type="time" value={v.window_end} onChange={(e) => setV({ ...v, window_end: e.target.value })} /></Field>
      </div>
    </Dialog>
  );
}
