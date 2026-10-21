import { useQuery } from "@tanstack/react-query";
import { Info } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { DateRangePicker, defaultRange } from "@/components/DateRange";
import { Alert, Card, CardBody, CardHeader, EmptyState, ErrorState, PageHeader, Select, Spinner, StatCard, StatusBadge, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/utils";
import type { Campaign, MetricSummary } from "@/types/api";

interface CampaignRow { campaign_id: number; name: string; status: string; dry_run: boolean; recipients: number; accepted: number; simulated: number; failures: number; hard_bounces: number; replies: number; positive_replies: number; opt_outs: number; contacted_leads: number; reply_rate: number | null; queued: number }
interface AccountRow { account_id: number; email: string; status: string; attempts: number; accepted: number; failures: number; hard_bounces: number; replies: number; simulated: number; bounce_rate: number | null }

const ORDER: [string, string, "num" | "pct"][] = [
  ["queued", "Emails queued", "num"],
  ["attempts", "Send attempts", "num"],
  ["accepted", "SMTP-accepted", "num"],
  ["delivered", "Confirmed deliveries", "num"],
  ["hard_bounces", "Hard bounces", "num"],
  ["soft_bounces", "Soft bounces", "num"],
  ["failures", "Failures", "num"],
  ["replies", "Replies", "num"],
  ["positive_replies", "Positive replies", "num"],
  ["opt_outs", "Opt-outs", "num"],
  ["contacted_leads", "Leads contacted", "num"],
  ["simulated", "Simulated (dry run)", "num"],
  ["reply_rate", "Reply rate", "pct"],
  ["positive_rate", "Positive rate", "pct"],
  ["bounce_rate", "Bounce rate", "pct"],
  ["opt_out_rate", "Opt-out rate", "pct"],
];

export function AnalyticsPage() {
  const [range, setRange] = useState(defaultRange(30));
  const [campaignId, setCampaignId] = useState("");
  const campaigns = useQuery({ queryKey: ["campaigns", ""], queryFn: () => api.get<Campaign[]>("/api/campaigns") });
  const summary = useQuery({ queryKey: ["analytics", "summary", range, campaignId], queryFn: () => api.get<MetricSummary>("/api/analytics/summary", { ...range, campaign_id: campaignId }) });
  const series = useQuery({ queryKey: ["analytics", "ts", range, campaignId], queryFn: () => api.get<Record<string, number | string>[]>("/api/analytics/timeseries", { ...range, campaign_id: campaignId }) });
  const perCampaign = useQuery({ queryKey: ["analytics", "campaigns", range], queryFn: () => api.get<CampaignRow[]>("/api/analytics/campaigns", { ...range }) });
  const perAccount = useQuery({ queryKey: ["analytics", "accounts", range], queryFn: () => api.get<AccountRow[]>("/api/analytics/accounts", { ...range }) });
  const m = summary.data?.metrics ?? {};
  const defs = summary.data?.definitions ?? {};

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Computed from send attempts, provider events, received replies and suppressions. Opens are not tracked."
        actions={
          <>
            <Select aria-label="Campaign" className="w-auto" value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
              <option value="">All campaigns</option>
              {campaigns.data?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
            <DateRangePicker value={range} onChange={setRange} />
          </>
        }
      />
      <Alert tone="info" className="mb-5" title="How to read these numbers">
        “SMTP-accepted” means your mail server took the message — not that it reached the inbox. Generic SMTP providers don't report deliveries, so “Confirmed deliveries” stays at 0 unless a provider sends delivery events. Rates use “Leads contacted” (distinct leads with an accepted message in the period) as the denominator; bounce rate uses accepted messages.
      </Alert>
      {summary.isLoading ? (
        <Spinner />
      ) : summary.isError ? (
        <ErrorState error={summary.error} />
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {ORDER.map(([k, label, kind]) => (
            <StatCard key={k} label={label} value={kind === "pct" ? fmtPct(m[k]) : fmtNum(m[k])} hint={<span className="inline-flex items-start gap-1"><Info className="mt-0.5 h-3 w-3 shrink-0" />{defs[k]}</span>} />
          ))}
        </div>
      )}
      <Card className="mt-6">
        <CardHeader title="Performance over time" />
        <CardBody className="h-72">
          {series.data?.some((r) => Number(r.accepted) + Number(r.simulated) + Number(r.replies) + Number(r.failures) > 0) ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={series.data} margin={{ left: -20, right: 8, top: 8 }}>
                <CartesianGrid stroke="#eef0f3" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#667085" }} tickFormatter={(v: string) => v.slice(5)} minTickGap={20} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "#667085" }} />
                <Tooltip contentStyle={{ borderRadius: 8, borderColor: "#e4e7ec", fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="accepted" name="Accepted" stackId="a" fill="#3b5bdb" />
                <Bar dataKey="simulated" name="Simulated" stackId="a" fill="#c3cad6" />
                <Bar dataKey="failures" name="Failures" stackId="a" fill="#f79009" />
                <Bar dataKey="bounces" name="Hard bounces" stackId="a" fill="#d92d20" />
                <Bar dataKey="replies" name="Replies" fill="#067647" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState title="No data in this range" />
          )}
        </CardBody>
      </Card>
      <Card className="mt-6">
        <CardHeader title="By campaign" />
        {perCampaign.data?.length ? (
          <Table>
            <thead>
              <tr>
                <Th>Campaign</Th>
                <Th>Status</Th>
                <Th className="text-right">Recipients</Th>
                <Th className="text-right">Accepted</Th>
                <Th className="text-right">Simulated</Th>
                <Th className="text-right">Failures</Th>
                <Th className="text-right">Bounces</Th>
                <Th className="text-right">Replies</Th>
                <Th className="text-right">Positive</Th>
                <Th className="text-right">Opt-outs</Th>
                <Th className="text-right">Reply rate</Th>
              </tr>
            </thead>
            <tbody>
              {perCampaign.data.map((c) => (
                <tr key={c.campaign_id}>
                  <Td><Link className="font-medium text-ink hover:text-brand" to={`/campaigns/${c.campaign_id}`}>{c.name}</Link></Td>
                  <Td><StatusBadge status={c.status} /></Td>
                  {[c.recipients, c.accepted, c.simulated, c.failures, c.hard_bounces, c.replies, c.positive_replies, c.opt_outs].map((n, i) => (
                    <Td key={i} className="tabular text-right">{fmtNum(n)}</Td>
                  ))}
                  <Td className="tabular text-right">{fmtPct(c.reply_rate)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState title="No campaigns" />
        )}
      </Card>
      <Card className="mt-6">
        <CardHeader title="By sending account" />
        {perAccount.data?.length ? (
          <Table>
            <thead>
              <tr>
                <Th>Account</Th>
                <Th>Status</Th>
                <Th className="text-right">Attempts</Th>
                <Th className="text-right">Accepted</Th>
                <Th className="text-right">Simulated</Th>
                <Th className="text-right">Failures</Th>
                <Th className="text-right">Bounces</Th>
                <Th className="text-right">Replies</Th>
                <Th className="text-right">Bounce rate</Th>
              </tr>
            </thead>
            <tbody>
              {perAccount.data.map((a) => (
                <tr key={a.account_id}>
                  <Td className="font-medium text-ink">{a.email}</Td>
                  <Td><StatusBadge status={a.status} /></Td>
                  {[a.attempts, a.accepted, a.simulated, a.failures, a.hard_bounces, a.replies].map((n, i) => (
                    <Td key={i} className="tabular text-right">{fmtNum(n)}</Td>
                  ))}
                  <Td className="tabular text-right">{fmtPct(a.bounce_rate)}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState title="No accounts" />
        )}
      </Card>
    </>
  );
}
