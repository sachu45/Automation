import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play, Radar } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, CardHeader, EmptyState, ErrorState, Spinner, StatCard, StatusBadge, Table, Td, Th } from "@/components/ui";
import { ConfirmDialog } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { fmtDate, fmtNum } from "@/lib/utils";
import type { DiscoveryRun, DiscoveryStatus } from "@/types/api";

export function useDiscoveryStatus() {
  return useQuery({ queryKey: ["discovery", "status"], queryFn: () => api.get<DiscoveryStatus>("/api/discovery/status"), refetchInterval: 30_000 });
}

export function DiscoveryOverview() {
  const qc = useQueryClient();
  const status = useDiscoveryStatus();
  const runs = useQuery({ queryKey: ["discovery", "runs"], queryFn: () => api.get<DiscoveryRun[]>("/api/discovery/runs"), refetchInterval: 30_000 });
  const failed = useQuery({
    queryKey: ["discovery", "failed"],
    queryFn: () => api.get<{ id: number; kind: string; attempts: number; last_error: string | null; finished_at: string | null; signal_id: number | null }[]>("/api/discovery/jobs/failed"),
  });
  const [confirmEnable, setConfirmEnable] = useState(false);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["discovery"] });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.patch("/api/discovery/config", { changes: { enabled }, confirm: enabled }),
    onSuccess: (_d, enabled) => {
      toast.success(enabled ? "Scheduled discovery enabled" : "Discovery paused");
      setConfirmEnable(false);
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const runNow = useMutation({
    mutationFn: () => api.post("/api/discovery/run", {}),
    onSuccess: () => {
      toast.success("Discovery run queued — the worker will pick it up shortly");
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (status.isLoading) return <Spinner />;
  if (status.isError || !status.data) return <ErrorState error={status.error} onRetry={() => status.refetch()} />;
  const s = status.data;

  return (
    <div className="space-y-6">
      <Alert tone="info" title="Discovery only finds signals and writes drafts">
        It never sends email, approves drafts or changes your sending settings. Every draft waits in <b>Draft review</b> for your approval of the exact text, and sending still goes through the
        normal send and campaign flows.
      </Alert>
      <Card className="flex flex-wrap items-center justify-between gap-4 p-5">
        <div className="flex items-center gap-3">
          <div className="rounded-xl bg-brand-soft p-2.5 text-brand">
            <Radar className="h-5 w-5" />
          </div>
          <div>
            <p className="font-semibold">
              Scheduled discovery {s.enabled ? <Badge tone="ok">on</Badge> : <Badge>paused</Badge>} {s.test_mode && <Badge tone="info">test mode</Badge>}
            </p>
            <p className="text-sm text-muted">
              Every {s.interval_minutes} min · next run {s.enabled ? fmtDate(s.next_run_at) : "— (paused)"} · last successful run {fmtDate(s.last_successful_run_at)}
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => runNow.mutate()} loading={runNow.isPending} disabled={s.queued_jobs > 0 && s.last_run?.status === "running"}>
            Run now
          </Button>
          {s.enabled ? (
            <Button onClick={() => toggle.mutate(false)} loading={toggle.isPending}>
              <Pause className="h-4 w-4" /> Pause
            </Button>
          ) : (
            <Button variant="primary" onClick={() => setConfirmEnable(true)}>
              <Play className="h-4 w-4" /> Enable schedule
            </Button>
          )}
        </div>
      </Card>
      {s.warnings.map((w) => (
        <Alert key={w} tone="warn">
          {w}
        </Alert>
      ))}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <StatCard label="Signals found" value={fmtNum(s.signals_total)} />
        <StatCard label="Qualified" value={fmtNum(s.qualified)} tone="ok" />
        <StatCard label="Needs review" value={fmtNum(s.by_processing_status.review_required ?? 0)} />
        <StatCard label="Drafts created" value={fmtNum(s.drafts_created)} hint={`${s.drafts_awaiting_review} awaiting review`} />
        <StatCard label="Duplicates skipped" value={fmtNum(s.duplicates_skipped)} />
        <StatCard label="Rejected" value={fmtNum(s.by_processing_status.rejected ?? 0)} />
        <StatCard label="Failed jobs" value={fmtNum(s.failed_jobs)} tone={s.failed_jobs ? "danger" : undefined} hint={`${s.queued_jobs} queued`} />
      </div>
      <Card>
        <CardHeader title="Recent runs" />
        {!runs.data?.length ? (
          <EmptyState title="No runs yet" description="Add feeds or company domains under Rules, then click Run now." />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Started</Th>
                <Th>Trigger</Th>
                <Th>Status</Th>
                <Th className="text-right">Found</Th>
                <Th className="text-right">New</Th>
                <Th className="text-right">Duplicates</Th>
                <Th>Problems</Th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.id}>
                  <Td className="whitespace-nowrap">{fmtDate(r.started_at)}</Td>
                  <Td>
                    {r.trigger}
                    {r.test_mode && <Badge tone="info" className="ml-1">test</Badge>}
                  </Td>
                  <Td>
                    <StatusBadge status={r.status === "partial" ? "needs_review" : r.status === "completed" ? "completed" : r.status === "failed" ? "failed" : "active"} label={r.status} />
                  </Td>
                  <Td className="tabular text-right">{r.stats.found ?? 0}</Td>
                  <Td className="tabular text-right">{r.stats.new ?? 0}</Td>
                  <Td className="tabular text-right">{r.stats.duplicates ?? 0}</Td>
                  <Td className="max-w-[360px] text-xs text-danger">{r.errors.map((e) => `${e.source}: ${e.message}`).join(" · ")}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {!!failed.data?.length && (
        <Card>
          <CardHeader title="Failed jobs" description="Permanent failures or retries exhausted. Fix the cause, then reprocess the signal from the Signals tab." />
          <ul className="divide-y divide-border text-sm">
            {failed.data.map((j) => (
              <li key={j.id} className="px-5 py-2.5">
                <span className="font-mono text-xs">{j.kind}</span> {j.signal_id && <>· signal #{j.signal_id}</>} · {j.attempts} attempt(s) · {fmtDate(j.finished_at)}
                <p className="text-xs text-danger">{j.last_error}</p>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <ConfirmDialog
        open={confirmEnable}
        onOpenChange={setConfirmEnable}
        title="Enable scheduled discovery?"
        description={`The worker will check your enabled sources every ${s.interval_minutes} minutes, within your per-run and daily limits. It only saves signals and drafts; nothing is sent.`}
        confirmLabel="Enable"
        loading={toggle.isPending}
        onConfirm={() => toggle.mutate(true)}
      />
    </div>
  );
}
