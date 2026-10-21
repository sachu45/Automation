import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Ban, Mail, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, KeyValue, PageHeader, Spinner, StatusBadge, Table, Td, Th } from "@/components/ui";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { SendEmailDialog } from "@/features/SendEmailDialog";
import { LeadForm, toPayload } from "@/features/leads/LeadForm";
import { api, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { Lead } from "@/types/api";

interface Activity {
  attempts: { id: number; campaign_id: number | null; step: number | null; status: string; simulation: boolean; subject: string | null; error: string | null; at: string }[];
  conversations: { id: number; subject: string | null; classification: string | null; last_message_at: string }[];
  audit: { action: string; summary: string; at: string }[];
}

export function LeadDetailPage() {
  const { id } = useParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [sending, setSending] = useState(false);
  const [confirm, setConfirm] = useState<"delete" | "dnc" | null>(null);
  const lead = useQuery({ queryKey: ["lead", id], queryFn: () => api.get<Lead>(`/api/leads/${id}`) });
  const activity = useQuery({ queryKey: ["lead", id, "activity"], queryFn: () => api.get<Activity>(`/api/leads/${id}/activity`) });

  const update = useMutation({
    mutationFn: (body: unknown) => api.patch<Lead>(`/api/leads/${id}`, body),
    onSuccess: () => {
      toast.success("Lead updated");
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["lead", id] });
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: () => api.delete(`/api/leads/${id}`),
    onSuccess: () => {
      toast.success("Lead deleted");
      qc.invalidateQueries({ queryKey: ["leads"] });
      navigate("/leads");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const dnc = useMutation({
    mutationFn: () => api.post("/api/leads/bulk", { lead_ids: [Number(id)], action: "do_not_contact", confirm: true }),
    onSuccess: () => {
      toast.success("Marked do-not-contact; scheduled messages cancelled");
      setConfirm(null);
      qc.invalidateQueries({ queryKey: ["lead", id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (lead.isLoading) return <Spinner />;
  if (lead.isError || !lead.data) return <ErrorState error={lead.error} />;
  const l = lead.data;
  const name = l.full_name || [l.first_name, l.last_name].filter(Boolean).join(" ") || l.email;

  return (
    <>
      <Link to="/leads" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
        <ArrowLeft className="h-4 w-4" /> Leads
      </Link>
      <PageHeader
        title={name}
        description={[l.job_title, l.company].filter(Boolean).join(" · ") || l.email}
        actions={
          <>
            <Button onClick={() => setEditing(true)}>
              <Pencil className="h-4 w-4" /> Edit
            </Button>
            <Button onClick={() => setConfirm("dnc")} disabled={l.suppressed}>
              <Ban className="h-4 w-4" /> Do not contact
            </Button>
            <Button variant="danger" onClick={() => setConfirm("delete")}>
              <Trash2 className="h-4 w-4" /> Delete
            </Button>
            <Button variant="primary" onClick={() => setSending(true)} disabled={l.suppressed}>
              <Mail className="h-4 w-4" /> Send email
            </Button>
          </>
        }
      />
      {l.suppressed && (
        <Alert tone="danger" className="mb-5" title="On the suppression list">
          This address will never be emailed. See the Suppression list for details.
        </Alert>
      )}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Details" />
          <CardBody>
            <KeyValue
              items={[
                ["Email", l.email],
                ["Status", <StatusBadge key="s" status={l.status} />],
                ["Company", l.company_website ? <a key="c" className="text-brand hover:underline" href={l.company_website} target="_blank" rel="noreferrer noopener">{l.company ?? l.company_website}</a> : l.company ?? "—"],
                ["Industry", l.industry ?? "—"],
                ["Country / timezone", [l.country, l.timezone].filter(Boolean).join(" · ") || "—"],
                ["LinkedIn", l.linkedin_url ? <a key="li" className="text-brand hover:underline" href={l.linkedin_url} target="_blank" rel="noreferrer noopener">Profile</a> : "—"],
                ["Source", l.source ?? "—"],
                ["Tags", l.tags.length ? <div key="t" className="flex flex-wrap gap-1">{l.tags.map((t) => <Badge key={t}>{t}</Badge>)}</div> : "—"],
                ["Personalization notes", l.notes ? <span key="n" className="whitespace-pre-wrap">{l.notes}</span> : "—"],
                ["Custom fields", Object.keys(l.custom_fields).length ? <code key="cf" className="whitespace-pre-wrap font-mono text-[13px]">{Object.entries(l.custom_fields).map(([k, v]) => `${k} = ${v}`).join("\n")}</code> : "—"],
                ["Last contacted", fmtDate(l.last_contacted_at)],
                ["Last reply", fmtDate(l.last_replied_at)],
                ["Added", fmtDate(l.created_at)],
              ]}
            />
          </CardBody>
        </Card>
        <div className="space-y-6">
          <Card>
            <CardHeader title="Campaigns" />
            {l.campaigns.length ? (
              <ul className="divide-y divide-border">
                {l.campaigns.map((c) => (
                  <li key={c.id} className="flex items-center justify-between gap-2 px-5 py-3 text-sm">
                    <Link className="font-medium hover:text-brand" to={`/campaigns/${c.id}`}>
                      {c.name}
                    </Link>
                    <StatusBadge status={c.recipient_status} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-5 py-4 text-sm text-muted">Not in any campaign.</p>
            )}
          </Card>
          <Card>
            <CardHeader title="Conversations" />
            {activity.data?.conversations.length ? (
              <ul className="divide-y divide-border">
                {activity.data.conversations.map((c) => (
                  <li key={c.id} className="px-5 py-3 text-sm">
                    <Link className="font-medium hover:text-brand" to={`/inbox?c=${c.id}`}>
                      {c.subject ?? "(no subject)"}
                    </Link>
                    <div className="mt-1 flex items-center gap-2 text-xs text-muted">
                      <StatusBadge status={c.classification} /> {fmtDate(c.last_message_at)}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-5 py-4 text-sm text-muted">No conversations yet.</p>
            )}
          </Card>
        </div>
      </div>

      <Card className="mt-6">
        <CardHeader title="Send attempts" description="Every attempt is recorded, including dry-run simulations and pre-send blocks." />
        {activity.data?.attempts.length ? (
          <Table>
            <thead>
              <tr>
                <Th>When</Th>
                <Th>Subject</Th>
                <Th>Step</Th>
                <Th>Status</Th>
                <Th>Detail</Th>
              </tr>
            </thead>
            <tbody>
              {activity.data.attempts.map((a) => (
                <tr key={a.id}>
                  <Td className="whitespace-nowrap">{fmtDate(a.at)}</Td>
                  <Td>{a.subject ?? "—"}</Td>
                  <Td>{a.step ?? "—"}</Td>
                  <Td>
                    <StatusBadge status={a.status} />
                    {a.simulation && a.status !== "simulated" && <Badge tone="info" className="ml-1">dry run</Badge>}
                  </Td>
                  <Td className="max-w-[320px] truncate text-xs" title={a.error ?? undefined}>{a.error ?? ""}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState title="No send attempts" />
        )}
      </Card>

      <Card className="mt-6">
        <CardHeader title="Audit trail" />
        {activity.data?.audit.length ? (
          <ul className="divide-y divide-border">
            {activity.data.audit.map((e, i) => (
              <li key={i} className="flex flex-wrap justify-between gap-2 px-5 py-2.5 text-sm">
                <span>{e.summary}</span>
                <span className="text-xs text-muted">{fmtDate(e.at)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="px-5 py-4 text-sm text-muted">No audit events.</p>
        )}
      </Card>

      <Dialog open={editing} onOpenChange={setEditing} title="Edit lead" size="lg">
        <LeadForm lead={l} onSubmit={(v) => update.mutate(toPayload(v))} submitting={update.isPending} onCancel={() => setEditing(false)} />
      </Dialog>
      <SendEmailDialog lead={l} open={sending} onOpenChange={setSending} />
      <ConfirmDialog
        open={confirm === "delete"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Delete this lead?"
        description="The lead and its list memberships are removed. Any suppression entry for this address is kept."
        tone="danger"
        confirmLabel="Delete lead"
        loading={del.isPending}
        onConfirm={() => del.mutate()}
      />
      <ConfirmDialog
        open={confirm === "dnc"}
        onOpenChange={(v) => !v && setConfirm(null)}
        title="Mark as do not contact?"
        description="The address is added to the suppression list and all scheduled messages to it are cancelled."
        tone="danger"
        confirmLabel="Suppress"
        loading={dnc.isPending}
        onConfirm={() => dnc.mutate()}
      />
    </>
  );
}
