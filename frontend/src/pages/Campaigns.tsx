import { useQuery } from "@tanstack/react-query";
import { Megaphone, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, PageHeader, Select, Spinner, StatusBadge, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { Campaign } from "@/types/api";

export function CampaignsPage() {
  const [status, setStatus] = useState("");
  const navigate = useNavigate();
  const campaigns = useQuery({ queryKey: ["campaigns", status], queryFn: () => api.get<Campaign[]>("/api/campaigns", { status }) });
  return (
    <>
      <PageHeader
        title="Campaigns"
        description="Draft → Ready for review → Approved → Scheduled/Active → Paused → Completed or Cancelled."
        actions={
          <>
            <Select aria-label="Filter by status" className="w-auto" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              {["draft", "ready_for_review", "approved", "scheduled", "active", "paused", "completed", "cancelled"].map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, " ")}
                </option>
              ))}
            </Select>
            <Link to="/campaigns/new">
              <Button variant="primary">
                <Plus className="h-4 w-4" /> New campaign
              </Button>
            </Link>
          </>
        }
      />
      <Card>
        {campaigns.isLoading ? (
          <Spinner />
        ) : campaigns.isError ? (
          <ErrorState error={campaigns.error} onRetry={() => campaigns.refetch()} />
        ) : !campaigns.data?.length ? (
          <EmptyState icon={<Megaphone className="h-6 w-6" />} title="No campaigns" description="A campaign sends a sequence to the leads in chosen lists or segments, within your sending window and limits." action={<Link to="/campaigns/new"><Button variant="primary">New campaign</Button></Link>} />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Status</Th>
                <Th>Mode</Th>
                <Th className="text-right">Recipients</Th>
                <Th className="text-right">Active</Th>
                <Th className="text-right">Replied</Th>
                <Th className="text-right">Completed</Th>
                <Th>Steps</Th>
                <Th>Created</Th>
              </tr>
            </thead>
            <tbody>
              {campaigns.data.map((c) => {
                const rc = c.recipient_counts;
                const total = Object.values(rc).reduce((a, b) => a + b, 0);
                const started = !["draft", "ready_for_review", "approved"].includes(c.status);
                return (
                  <tr key={c.id} className="cursor-pointer hover:bg-bg/60" onClick={() => navigate(`/campaigns/${c.id}`)}>
                    <Td className="font-medium text-ink">{c.name}</Td>
                    <Td>
                      <StatusBadge status={c.status} />
                    </Td>
                    <Td>{started ? <Badge tone={c.dry_run ? "info" : "ok"}>{c.dry_run ? "dry run" : "live"}</Badge> : <span className="text-faint">—</span>}</Td>
                    <Td className="tabular text-right">{total || "—"}</Td>
                    <Td className="tabular text-right">{rc.active ?? 0}</Td>
                    <Td className="tabular text-right">{rc.replied ?? 0}</Td>
                    <Td className="tabular text-right">{rc.completed ?? 0}</Td>
                    <Td>{c.step_count || "—"}</Td>
                    <Td className="whitespace-nowrap">{fmtDate(c.created_at, false)}</Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
