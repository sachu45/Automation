import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { useAppSettings } from "@/app/Layout";
import { Alert, Badge, Button, Card, CardBody, CardHeader, Checkbox, Field, Input, KeyValue, PageHeader, Pagination, Select, Spinner, Switch, Table, Tabs, Td, Textarea, Th } from "@/components/ui";
import { ConfirmDialog } from "@/components/ui/dialog";
import { api, ApiError, errorMessage } from "@/lib/api";
import { cn, fmtDate, titleCase, WEEKDAYS } from "@/lib/utils";
import type { AppConfig } from "@/types/api";

type Tab = "sender" | "sending" | "ai" | "data" | "audit" | "security";

export function SettingsPage() {
  const [tab, setTab] = useState<Tab>("sender");
  const settings = useAppSettings();
  return (
    <>
      <PageHeader title="Settings" description="Sender identity, safety controls, AI, data retention, and security." />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "sender", label: "Sender identity" },
          { value: "sending", label: "Sending & safety" },
          { value: "ai", label: "AI" },
          { value: "data", label: "Data & retention" },
          { value: "audit", label: "Audit log" },
          { value: "security", label: "Security & backups" },
        ]}
      />
      {settings.isLoading || !settings.data ? (
        <Spinner />
      ) : tab === "audit" ? (
        <AuditLog />
      ) : tab === "security" ? (
        <SecurityTab env={settings.data.environment} />
      ) : (
        <ConfigForm tab={tab} config={settings.data.config} phrase={settings.data.live_sending_phrase} ai={settings.data.ai} env={settings.data.environment} />
      )}
    </>
  );
}

function useSaveConfig() {
  const qc = useQueryClient();
  const [pending, setPending] = useState<{ changes: Partial<AppConfig>; fields: string[] } | null>(null);
  const mut = useMutation({
    mutationFn: (p: { changes: Partial<AppConfig>; confirm?: boolean; phrase?: string }) =>
      api.patch("/api/settings", { changes: p.changes, confirm_safety: !!p.confirm, confirmation_phrase: p.phrase ?? null }),
    onSuccess: () => {
      toast.success("Settings saved");
      setPending(null);
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["ai-status"] });
    },
    onError: (e, vars) => {
      if (e instanceof ApiError && e.code === "confirmation_required" && !vars.confirm) {
        const d = e.details as { fields?: string[] };
        setPending({ changes: vars.changes, fields: d?.fields ?? [] });
      } else toast.error(errorMessage(e));
    },
  });
  return { mut, pending, setPending };
}

function ConfigForm({ tab, config, phrase, ai, env }: { tab: Tab; config: AppConfig; phrase: string; ai: { provider: string; has_api_key: boolean; model: string | null; ai_enabled: boolean }; env: { unsubscribe_links: boolean; public_base_url: string | null } }) {
  const [v, setV] = useState<AppConfig>(config);
  const { mut, pending, setPending } = useSaveConfig();
  useEffect(() => setV(config), [config]);
  const set = (patch: Partial<AppConfig>) => setV({ ...v, ...patch });
  const changed = (Object.keys(v) as (keyof AppConfig)[]).filter((k) => JSON.stringify(v[k]) !== JSON.stringify(config[k]));
  const save = () => mut.mutate({ changes: Object.fromEntries(changed.map((k) => [k, v[k]])) as Partial<AppConfig> });

  return (
    <>
      {tab === "sender" && (
        <Card>
          <CardHeader title="Sender identity" description="Shown to recipients. Be honest and complete — many laws require a real identity and postal address in commercial email." />
          <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="Sender name" htmlFor="s1"><Input id="s1" value={v.sender_name} onChange={(e) => set({ sender_name: e.target.value })} /></Field>
            <Field label="Default sender email" htmlFor="s2"><Input id="s2" type="email" value={v.default_sender_email ?? ""} onChange={(e) => set({ default_sender_email: e.target.value || null })} /></Field>
            <Field label="Reply-to email" htmlFor="s3"><Input id="s3" type="email" value={v.reply_to_email ?? ""} onChange={(e) => set({ reply_to_email: e.target.value || null })} /></Field>
            <Field label="Company name" htmlFor="s4"><Input id="s4" value={v.company_name} onChange={(e) => set({ company_name: e.target.value })} /></Field>
            <Field label="Company website" htmlFor="s5"><Input id="s5" value={v.company_website} onChange={(e) => set({ company_website: e.target.value })} /></Field>
            <Field label="Postal address" htmlFor="s6" hint="Appended to the opt-out footer when set"><Input id="s6" value={v.mailing_address} onChange={(e) => set({ mailing_address: e.target.value })} /></Field>
            <Field label="Signature" htmlFor="s7" hint="Used by {{signature}}; falls back to the account display name" className="md:col-span-2"><Textarea id="s7" rows={4} value={v.signature} onChange={(e) => set({ signature: e.target.value })} /></Field>
          </CardBody>
        </Card>
      )}
      {tab === "sending" && (
        <div className="space-y-6">
          <Card>
            <CardHeader title="Mode" />
            <CardBody className="space-y-3">
              <div className="flex items-center justify-between gap-4 rounded-lg border border-border p-4">
                <div>
                  <p className="font-medium">Dry-run mode {v.dry_run ? <Badge tone="info">on</Badge> : <Badge tone="ok">off — live</Badge>}</p>
                  <p className="text-sm text-muted">When on, campaigns and single sends are rendered and recorded as simulations; SMTP is never contacted. Campaigns keep the mode they were activated with.</p>
                </div>
                <Switch label="Dry-run mode" checked={v.dry_run} onChange={(x) => set({ dry_run: x })} />
              </div>
              {!v.dry_run && config.dry_run && <Alert tone="warn">Turning dry-run off requires typing “{phrase}” when you save.</Alert>}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Limits" description="The strictest of global, account and campaign limits always applies. Raising a limit requires confirmation." />
            <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <Field label="Global daily sending limit" htmlFor="l1"><Input id="l1" type="number" min={1} value={v.daily_sending_limit} onChange={(e) => set({ daily_sending_limit: Number(e.target.value) })} /></Field>
              <Field label="Minimum seconds between messages" htmlFor="l2"><Input id="l2" type="number" min={10} value={v.min_delay_seconds} onChange={(e) => set({ min_delay_seconds: Number(e.target.value) })} /></Field>
              <Field label="Default follow-up delay (days)" htmlFor="l3"><Input id="l3" type="number" min={1} value={v.followup_default_delay_days} onChange={(e) => set({ followup_default_delay_days: Number(e.target.value) })} /></Field>
              <Field label="Max follow-ups (default)" htmlFor="l4"><Input id="l4" type="number" min={0} max={10} value={v.max_followups} onChange={(e) => set({ max_followups: Number(e.target.value) })} /></Field>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Default sending window" />
            <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <Field label="Default timezone" htmlFor="w1" hint="IANA name, e.g. America/New_York"><Input id="w1" value={v.default_timezone} onChange={(e) => set({ default_timezone: e.target.value })} /></Field>
              <Field label="Window start" htmlFor="w2"><Input id="w2" type="time" value={v.default_window_start} onChange={(e) => set({ default_window_start: e.target.value })} /></Field>
              <Field label="Window end" htmlFor="w3"><Input id="w3" type="time" value={v.default_window_end} onChange={(e) => set({ default_window_end: e.target.value })} /></Field>
              <div className="flex flex-wrap gap-1.5 md:col-span-3">
                {WEEKDAYS.map((d, i) => {
                  const on = v.default_send_days.includes(i);
                  return (
                    <button key={d} type="button" aria-pressed={on} onClick={() => set({ default_send_days: on ? v.default_send_days.filter((x) => x !== i) : [...v.default_send_days, i].sort() })} className={cn("rounded-lg px-3 py-1.5 text-sm ring-1", on ? "bg-brand text-white ring-brand" : "bg-surface text-ink-2 ring-border-strong")}>
                      {d}
                    </button>
                  );
                })}
              </div>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Opt-outs & replies" />
            <CardBody className="space-y-4">
              <label className="flex items-start gap-2 text-sm"><Checkbox checked={v.append_opt_out_footer} onChange={(e) => set({ append_opt_out_footer: e.target.checked })} className="mt-0.5" /> <span>Append an opt-out footer to every outreach email (unless the template includes {"{{opt_out_text}}"} or {"{{unsubscribe_url}}"})</span></label>
              <Field label="Opt-out text" htmlFor="o1"><Textarea id="o1" rows={2} value={v.opt_out_text} onChange={(e) => set({ opt_out_text: e.target.value })} /></Field>
              <Alert tone={env.unsubscribe_links ? "ok" : "info"}>
                {env.unsubscribe_links ? <>One-click unsubscribe links are enabled via {env.public_base_url}.</> : <>Unsubscribe links are off because PUBLIC_BASE_URL isn't set; recipients opt out by replying (List-Unsubscribe mailto header is still sent). See README to expose the /u/ endpoint over HTTPS.</>}
              </Alert>
              <label className="flex items-center gap-2 text-sm"><Checkbox checked={v.auto_suppress_unsubscribe_replies} onChange={(e) => set({ auto_suppress_unsubscribe_replies: e.target.checked })} /> Automatically suppress replies classified as unsubscribe requests</label>
              <label className="flex items-center gap-2 text-sm"><Checkbox checked={v.stop_on_out_of_office} onChange={(e) => set({ stop_on_out_of_office: e.target.checked })} /> Stop sequences on out-of-office replies too</label>
            </CardBody>
          </Card>
        </div>
      )}
      {tab === "ai" && <AiSettings v={v} set={set} ai={ai} />}
      {tab === "data" && (
        <Card>
          <CardHeader title="Data retention" description="Message bodies older than these limits are erased hourly by the worker. 0 = keep forever. Metadata (dates, statuses) is kept for analytics." />
          <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Field label="Inbound message bodies (days)" htmlFor="r1"><Input id="r1" type="number" min={0} value={v.retention_inbound_body_days} onChange={(e) => set({ retention_inbound_body_days: Number(e.target.value) })} /></Field>
            <Field label="Outbound message bodies (days)" htmlFor="r2"><Input id="r2" type="number" min={0} value={v.retention_outbound_body_days} onChange={(e) => set({ retention_outbound_body_days: Number(e.target.value) })} /></Field>
            <Field label="Audit events (days)" htmlFor="r3"><Input id="r3" type="number" min={0} value={v.retention_audit_days} onChange={(e) => set({ retention_audit_days: Number(e.target.value) })} /></Field>
            <p className="text-xs text-muted md:col-span-3">Deleting a lead removes its record; suppression entries are kept so that person is never contacted again. Raw CSV uploads are purged after import (or after 24h if abandoned).</p>
          </CardBody>
        </Card>
      )}
      <div className="sticky bottom-0 mt-6 flex items-center justify-end gap-3 border-t border-border bg-bg/90 py-3 backdrop-blur">
        {changed.length > 0 && <span className="text-sm text-muted">{changed.length} unsaved change(s)</span>}
        <Button onClick={() => setV(config)} disabled={!changed.length}>Discard</Button>
        <Button variant="primary" onClick={save} disabled={!changed.length} loading={mut.isPending}>Save settings</Button>
      </div>
      <ConfirmDialog
        open={!!pending}
        onOpenChange={(o) => !o && setPending(null)}
        title="Confirm safety change"
        description={`These changes relax safety defaults: ${pending?.fields.map(titleCase).join(", ")}.`}
        tone="danger"
        confirmLabel="Confirm change"
        typeToConfirm={pending?.fields.includes("dry_run") ? phrase : undefined}
        loading={mut.isPending}
        onConfirm={(typed) => pending && mut.mutate({ changes: pending.changes, confirm: true, phrase: typed })}
      />
    </>
  );
}

function AiSettings({ v, set, ai }: { v: AppConfig; set: (p: Partial<AppConfig>) => void; ai: { provider: string; has_api_key: boolean; model: string | null; ai_enabled: boolean } }) {
  const qc = useQueryClient();
  const [key, setKey] = useState("");
  const usage = useQuery({ queryKey: ["ai-usage"], queryFn: () => api.get<{ by_purpose: { purpose: string; status: string; requests: number; prompt_tokens: number; completion_tokens: number }[]; recent_errors: { at: string; purpose: string; error: string }[] }>("/api/ai/usage") });
  const saveKey = useMutation({
    mutationFn: (k: string | null) => api.put("/api/settings/ai-key", { api_key: k }),
    onSuccess: () => {
      toast.success("API key updated (stored encrypted)");
      setKey("");
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="AI provider" description="Optional. Without a key the app uses deterministic template mode. AI only drafts text; it can never approve or send." />
        <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <Field label="Provider" htmlFor="a1">
            <Select id="a1" value={v.ai_provider} onChange={(e) => set({ ai_provider: e.target.value })}>
              <option value="env">Use environment config (AI_PROVIDER)</option>
              <option value="template">Template mode (no AI)</option>
              <option value="openai_compatible">OpenAI-compatible API</option>
            </Select>
          </Field>
          <Field label="API base URL" htmlFor="a2" hint="Blank = AI_BASE_URL from environment"><Input id="a2" value={v.ai_base_url} placeholder="https://api.openai.com/v1" onChange={(e) => set({ ai_base_url: e.target.value })} /></Field>
          <Field label="Model" htmlFor="a3" hint="Blank = AI_MODEL from environment"><Input id="a3" value={v.ai_model} onChange={(e) => set({ ai_model: e.target.value })} /></Field>
          <label className="flex items-center gap-2 text-sm md:col-span-3"><Checkbox checked={v.ai_classify_replies} onChange={(e) => set({ ai_classify_replies: e.target.checked })} /> Use AI to suggest reply classifications (reply text is sent to the provider; suggestions still need review)</label>
          <div className="md:col-span-3">
            <KeyValue items={[["Effective provider", ai.provider], ["Model", ai.model ?? "—"], ["API key", ai.has_api_key ? "configured (hidden)" : "not set"], ["Status", ai.ai_enabled ? <Badge key="s" tone="ok">AI enabled</Badge> : <Badge key="s">template mode</Badge>]]} />
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="API key" description="Stored encrypted in the database. It is never displayed or logged. An AI_API_KEY environment variable is used if no key is stored here." />
        <CardBody className="flex flex-wrap gap-2">
          <Input aria-label="API key" type="password" autoComplete="off" className="max-w-md" placeholder="sk-…" value={key} onChange={(e) => setKey(e.target.value)} />
          <Button variant="primary" disabled={!key} loading={saveKey.isPending} onClick={() => saveKey.mutate(key)}>Save key</Button>
          {ai.has_api_key && <Button onClick={() => saveKey.mutate(null)}>Remove stored key</Button>}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Usage" description="Request metadata only — prompts and outputs aren't stored in usage logs." />
        <Table>
          <thead><tr><Th>Purpose</Th><Th>Status</Th><Th className="text-right">Requests</Th><Th className="text-right">Prompt tokens</Th><Th className="text-right">Completion tokens</Th></tr></thead>
          <tbody>
            {usage.data?.by_purpose.map((u) => (
              <tr key={u.purpose + u.status}><Td>{u.purpose}</Td><Td>{u.status}</Td><Td className="tabular text-right">{u.requests}</Td><Td className="tabular text-right">{u.prompt_tokens}</Td><Td className="tabular text-right">{u.completion_tokens}</Td></tr>
            ))}
            {!usage.data?.by_purpose.length && <tr><Td colSpan={5} className="text-center text-muted">No AI requests yet</Td></tr>}
          </tbody>
        </Table>
        {!!usage.data?.recent_errors.length && (
          <CardBody>
            <p className="mb-2 text-sm font-medium">Recent errors</p>
            <ul className="space-y-1 text-xs text-danger">{usage.data.recent_errors.map((e, i) => <li key={i}>{fmtDate(e.at)} — {e.purpose}: {e.error}</li>)}</ul>
          </CardBody>
        )}
      </Card>
    </div>
  );
}

function AuditLog() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const log = useQuery({ queryKey: ["audit", page, action], queryFn: () => api.get<{ items: { id: number; action: string; summary: string; created_at: string }[]; total: number }>("/api/settings/audit", { page, action, page_size: 50 }) });
  return (
    <Card>
      <div className="flex justify-end border-b border-border p-3">
        <Select aria-label="Filter actions" className="w-auto" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }}>
          <option value="">All actions</option>
          {["campaign", "lead", "suppression", "account", "settings", "auth", "template", "import", "send", "inbox", "ai_draft"].map((a) => <option key={a} value={a}>{a}</option>)}
        </Select>
      </div>
      {log.isLoading ? <Spinner /> : (
        <>
          <Table>
            <thead><tr><Th>When</Th><Th>Action</Th><Th>Summary</Th></tr></thead>
            <tbody>
              {log.data?.items.map((e) => (
                <tr key={e.id}><Td className="whitespace-nowrap">{fmtDate(e.created_at)}</Td><Td className="font-mono text-xs">{e.action}</Td><Td>{e.summary}</Td></tr>
              ))}
            </tbody>
          </Table>
          <Pagination page={page} pageSize={50} total={log.data?.total ?? 0} onPage={setPage} />
        </>
      )}
    </Card>
  );
}

function SecurityTab({ env }: { env: { app_env: string; database: string; bind_host: string; cookie_secure: boolean } }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const change = useMutation({
    mutationFn: () => api.post("/api/auth/change-password", { current_password: current, new_password: next }),
    onSuccess: () => { toast.success("Password changed; other sessions were signed out"); setCurrent(""); setNext(""); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const retention = useMutation({ mutationFn: () => api.post<Record<string, number>>("/api/settings/retention/run"), onSuccess: (r) => toast.success(`Retention applied: ${JSON.stringify(r)}`), onError: (e) => toast.error(errorMessage(e)) });
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader title="Change admin password" />
        <CardBody className="space-y-3">
          <Field label="Current password" htmlFor="cp1"><Input id="cp1" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
          <Field label="New password" htmlFor="cp2" hint="At least 10 characters"><Input id="cp2" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
          <Button variant="primary" disabled={!current || next.length < 10} loading={change.isPending} onClick={() => change.mutate()}>Change password</Button>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Environment" />
        <CardBody className="space-y-3">
          <KeyValue items={[["Environment", env.app_env], ["Database", env.database], ["Bound to", env.bind_host], ["Secure cookies", env.cookie_secure ? "yes (HTTPS)" : "no (local HTTP)"]]} />
          {!["127.0.0.1", "localhost", "::1"].includes(env.bind_host) && !env.cookie_secure && <Alert tone="warn">The server isn't bound to localhost but cookies aren't marked Secure. Put it behind HTTPS and set COOKIE_SECURE=true.</Alert>}
          <Button onClick={() => retention.mutate()} loading={retention.isPending}>Run retention now</Button>
        </CardBody>
      </Card>
      <Card className="lg:col-span-2">
        <CardHeader title="Backups & restore" />
        <CardBody className="space-y-2 text-sm text-ink-2">
          <p><b>SQLite (local):</b> stop the API and worker, then run <code className="font-mono">python scripts/backup.py</code> from the repo root (or copy <code className="font-mono">backend/data/outreach.db</code>). Also back up <code className="font-mono">backend/data/encryption.key</code> and <code className="font-mono">secret.key</code> — without the encryption key, stored account credentials can't be decrypted.</p>
          <p><b>PostgreSQL (Docker):</b> <code className="font-mono">docker compose exec db pg_dump -U outreach outreach &gt; backup.sql</code>; restore with <code className="font-mono">psql</code> into an empty database, then run migrations.</p>
          <p>Store backups encrypted; they contain personal data. Full instructions: <code className="font-mono">docs/OPERATIONS.md</code>.</p>
        </CardBody>
      </Card>
    </div>
  );
}
