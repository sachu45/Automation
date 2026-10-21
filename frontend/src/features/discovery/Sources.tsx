import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, Checkbox, EmptyState, ErrorState, Input, KeyValue, Spinner } from "@/components/ui";
import { ConfirmDialog } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { DiscoveryConfig, DiscoveryProvider } from "@/types/api";

export function DiscoverySources({ config }: { config: DiscoveryConfig }) {
  const providers = useQuery({ queryKey: ["discovery", "providers"], queryFn: () => api.get<DiscoveryProvider[]>("/api/discovery/providers") });
  if (providers.isLoading) return <Spinner />;
  if (providers.isError) return <ErrorState error={providers.error} onRetry={() => providers.refetch()} />;
  if (!providers.data?.length) return <EmptyState title="No providers" />;
  return (
    <div className="space-y-4">
      <Alert tone="info" title="What works without any keys">
        RSS/Atom feeds, company websites and manual signals (including LinkedIn posts you paste yourself) work with no API keys. Drafts then use the built-in template writer. Apollo, Google
        Custom Search and Gemini are optional extras. LinkedIn isn't fetched or scraped; no authorised LinkedIn integration is configured.
      </Alert>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {providers.data.map((p) => (
          <ProviderCard key={p.name} p={p} config={config} />
        ))}
      </div>
    </div>
  );
}

function ProviderCard({ p, config }: { p: DiscoveryProvider; config: DiscoveryConfig }) {
  const qc = useQueryClient();
  const [key, setKey] = useState("");
  const [confirmEnable, setConfirmEnable] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["discovery"] });
  const saveKey = useMutation({
    mutationFn: (k: string | null) => api.put(`/api/discovery/providers/${p.name}/key`, { api_key: k }),
    onSuccess: () => {
      toast.success("Key saved (stored encrypted; it won't be shown again)");
      setKey("");
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const test = useMutation({
    mutationFn: () => api.post<{ ok: boolean; message: string }>(`/api/discovery/providers/${p.name}/test`),
    onSuccess: (r) => {
      setResult(r);
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => {
      const next = enabled ? [...new Set([...config.enabled_sources, p.name])] : config.enabled_sources.filter((s) => s !== p.name);
      return api.patch("/api/discovery/config", { changes: { enabled_sources: next } });
    },
    onSuccess: () => {
      toast.success("Sources updated");
      setConfirmEnable(false);
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const isSource = ["rss", "website", "apollo", "google_cse", "manual"].includes(p.name);
  const u = p.usage;

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold">{p.label}</h3>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {p.configured ? <Badge tone="ok">configured</Badge> : <Badge tone="warn">not configured</Badge>}
            {p.requires_key ? <Badge>API key</Badge> : <Badge tone="info">no key needed</Badge>}
            {p.key_source && <Badge>key from {p.key_source}</Badge>}
            {p.name === "gemini" && (p.active_for_drafting ? <Badge tone="ok">used for drafting</Badge> : <Badge>not selected in Settings → AI</Badge>)}
          </div>
        </div>
        {isSource && p.name !== "manual" && (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={p.enabled} onChange={(e) => (e.target.checked && p.requires_key ? setConfirmEnable(true) : toggle.mutate(e.target.checked))} /> Enabled
          </label>
        )}
      </div>
      <p className="mt-3 text-sm text-ink-2">{p.cost}</p>
      <div className="mt-3">
        <KeyValue
          items={[
            ["Today", `${u.requests_today} request(s)${u.credits_today ? ` · ${u.credits_today} credit(s)` : ""}${u.errors_today ? ` · ${u.errors_today} error(s)` : ""}`],
            ["Last success", fmtDate(u.last_success_at)],
            ["Last error", u.last_error ? <span key="e" className="text-danger">{u.last_error} ({fmtDate(u.last_error_at)})</span> : "—"],
            ...(p.name === "rss" ? [["Feeds", String(p.feeds ?? 0)] as [string, string]] : []),
            ...(p.name === "website" ? [["Watched domains", String(p.watch_domains ?? 0)] as [string, string]] : []),
            ...(p.name === "gemini" ? [["Model", p.model ?? "—"] as [string, string]] : []),
            ...(p.name === "google_cse" ? [["Engine ID", p.engine_id_configured ? "set (GOOGLE_SEARCH_ENGINE_ID)" : "not set"] as [string, string]] : []),
            ...(p.name === "apollo" ? [["Enrichment", p.enrichment_enabled ? `on · ${p.credits_used_today ?? 0}/${p.credit_budget ?? 0} credits today` : "off"] as [string, string]] : []),
          ]}
        />
      </div>
      {result && <Alert tone={result.ok ? "ok" : "danger"} className="mt-3">{result.message}</Alert>}
      {p.requires_key && (
        <div className="mt-4 flex flex-wrap gap-2">
          <Input aria-label={`${p.label} API key`} type="password" autoComplete="off" className="max-w-xs" placeholder={p.configured ? "Replace key…" : "Paste API key"} value={key} onChange={(e) => setKey(e.target.value)} />
          <Button onClick={() => saveKey.mutate(key)} disabled={!key.trim()} loading={saveKey.isPending}>Save key</Button>
          {p.key_source === "stored" && <Button variant="ghost" onClick={() => saveKey.mutate(null)}>Remove stored key</Button>}
          <Button onClick={() => test.mutate()} disabled={!p.configured} loading={test.isPending}>Test connection</Button>
        </div>
      )}
      <ConfirmDialog
        open={confirmEnable}
        onOpenChange={setConfirmEnable}
        title={`Enable ${p.label}?`}
        description={`${p.cost} Requests count against your provider plan's limits.`}
        confirmLabel="Enable"
        loading={toggle.isPending}
        onConfirm={() => toggle.mutate(true)}
      />
    </Card>
  );
}
