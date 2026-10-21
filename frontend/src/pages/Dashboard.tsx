import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { DateRangePicker, defaultRange } from "@/components/DateRange";
import { Alert, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, PageHeader, Spinner, StatCard, StatusBadge, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/utils";
import type { MetricSummary } from "@/types/api";

interface CampaignRow {
  campaign_id: number;
  name: string;
  status: string;
  dry_run: boolean;
  recipients: number;
  accepted: number;
  simulated: number;
  replies: number;
  positive_replies: number;
  hard_bounces: number;
  opt_outs: number;
  reply_rate: number | null;
  queued: number;
}

export function DashboardPage() {
  const [range, setRange] = useState(defaultRange(30));
  const summary = useQuery({ queryKey: ["analytics", "summary", range], queryFn: () => api.get<MetricSummary>("/api/analytics/summary", { ...range }) });
  const series = useQuery({
    queryKey: ["analytics", "timeseries", range],
    queryFn: () => api.get<Record<string, number | string>[]>("/api/analytics/timeseries", { ...range }),
  });
  const campaigns = useQuery({ queryKey: ["analytics", "campaigns", range], queryFn: () => api.get<CampaignRow[]>("/api/analytics/campaigns", { ...range }) });
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<{ worker_suspected_down: boolean; oldest_due_job_seconds: number }>("/api/health"), refetchInterval: 60_000 });

  const m = summary.data?.metrics ?? {};
  const d = summary.data?.definitions ?? {};

  return (
    <>
      <PageHeader title="Dashboard" description="Live numbers from your database. SMTP acceptance is not proof of inbox delivery." actions={<DateRangePicker value={range} onChange={setRange} />} />
      {health.data?.worker_suspected_down && (
        <Alert tone="warn" title="The background worker may not be running" className="mb-5">
          Scheduled jobs have been waiting {Math.round(health.data.oldest_due_job_seconds / 60)} minutes. Start it with <code className="font-mono">python -m app.workers.run</code>.
        </Alert>
      )}
      {summary.isLoading ? (
        <Spinner />
      ) : summary.isError ? (
        <ErrorState error={summary.error} onRetry={() => summary.refetch()} />
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          <StatCard label="Total leads" value={fmtNum(m.total_leads)} title={d.total_leads} />
          <StatCard label="Ready for outreach" value={fmtNum(m.ready_leads)} title={d.ready_leads} />
          <StatCard label="Emails sent (attempted)" value={fmtNum(m.attempts)} title={d.attempts} hint={`${fmtNum(m.simulated)} simulated`} />
          <StatCard label="Accepted by SMTP" value={fmtNum(m.accepted)} title={d.accepted} hint="Not proof of delivery" />
          <StatCard label="Delivery failures" value={fmtNum((m.failures ?? 0) + (m.hard_bounces ?? 0))} title={`${d.failures} + ${d.hard_bounces}`} tone={(m.hard_bounces ?? 0) > 0 ? "danger" : undefined} hint={`${fmtNum(m.hard_bounces)} hard bounces`} />
          <StatCard label="Replies" value={fmtNum(m.replies)} title={d.replies} hint={`${fmtPct(m.reply_rate)} of contacted`} />
          <StatCard label="Positive replies" value={fmtNum(m.positive_replies)} title={d.positive_replies} tone="ok" hint={`${fmtPct(m.positive_rate)} of contacted`} />
          <StatCard label="Opt-outs" value={fmtNum(m.opt_outs)} title={d.opt_outs} hint={`${fmtPct(m.opt_out_rate)} of contacted`} />
          <StatCard label="Active campaigns" value={fmtNum(m.active_campaigns)} title={d.active_campaigns} />
          <StatCard label="Scheduled follow-ups" value={fmtNum(m.scheduled_followups)} title={d.scheduled_followups} hint={`${fmtNum(m.queued)} jobs queued`} />
        </div>
      )}

      <Card className="mt-6">
        <CardHeader title="Activity over time" description="Accepted, simulated (dry-run) and replies per day" />
        <CardBody className="h-72">
          {series.data && series.data.some((r) => Number(r.accepted) + Number(r.simulated) + Number(r.replies) > 0) ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={series.data} margin={{ left: -20, right: 8, top: 8 }}>
                <CartesianGrid stroke="#eef0f3" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#667085" }} tickFormatter={(v: string) => v.slice(5)} minTickGap={24} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#667085" }} />
                <Tooltip contentStyle={{ borderRadius: 8, borderColor: "#e4e7ec", fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Area type="monotone" dataKey="accepted" name="Accepted" stroke="#3b5bdb" fill="#3b5bdb" fillOpacity={0.12} strokeWidth={2} />
                <Area type="monotone" dataKey="simulated" name="Simulated" stroke="#98a2b3" fill="#98a2b3" fillOpacity={0.08} strokeDasharray="4 3" />
                <Area type="monotone" dataKey="replies" name="Replies" stroke="#067647" fill="#067647" fillOpacity={0.12} strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState title="No sending activity in this range" description="Activity appears here once a campaign sends (or simulates) messages." />
          )}
        </CardBody>
      </Card>

      <Card className="mt-6">
        <CardHeader
          title="Campaigns"
          actions={
            <Link to="/campaigns">
              <Button size="sm" variant="ghost">
                All campaigns <ArrowRight className="h-3.5 w-3.5" />
              </Button>
            </Link>
          }
        />
        {campaigns.isLoading ? (
          <Spinner />
        ) : !campaigns.data?.length ? (
          <EmptyState
            title="No campaigns yet"
            description="Import leads, write a template, build a sequence, then create a campaign."
            action={
              <Link to="/campaigns/new">
                <Button variant="primary">New campaign</Button>
              </Link>
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Campaign</Th>
                <Th>Status</Th>
                <Th className="text-right">Recipients</Th>
                <Th className="text-right">Accepted</Th>
                <Th className="text-right">Replies</Th>
                <Th className="text-right">Positive</Th>
                <Th className="text-right">Bounces</Th>
                <Th className="text-right">Opt-outs</Th>
                <Th className="text-right">Reply rate</Th>
              </tr>
            </thead>
            <tbody>
              {campaigns.data.slice(0, 8).map((c) => (
                <tr key={c.campaign_id} className="hover:bg-bg/50">
                  <Td>
                    <Link to={`/campaigns/${c.campaign_id}`} className="font-medium text-ink hover:text-brand">
                      {c.name}
                    </Link>
                    {c.dry_run && c.simulated > 0 && <span className="ml-2 text-xs text-muted">({c.simulated} simulated)</span>}
                  </Td>
                  <Td>
                    <StatusBadge status={c.status} />
                  </Td>
                  <Td className="tabular text-right">{fmtNum(c.recipients)}</Td>
                  <Td className="tabular text-right">{fmtNum(c.accepted)}</Td>
                  <Td className="tabular text-right">{fmtNum(c.replies)}</Td>
                  <Td className="tabular text-right">{fmtNum(c.positive_replies)}</Td>
                  <Td className="tabular text-right">{fmtNum(c.hard_bounces)}</Td>
                  <Td className="tabular text-right">{fmtNum(c.opt_outs)}</Td>
                  <Td className="tabular text-right">{fmtPct(c.reply_rate)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
