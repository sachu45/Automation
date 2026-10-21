import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plug, Plus, RefreshCw, Send, ShieldCheck, Trash2, Unplug } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, Checkbox, EmptyState, Field, Input, KeyValue, PageHeader, Select, Spinner, StatusBadge, Switch } from "@/components/ui";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { Account } from "@/types/api";

interface ProviderInfo {
  provider: string;
  label: string;
  oauth_configured: boolean | null;
  scopes?: string[];
  permissions: string;
}

export function AccountsPage() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api.get<Account[]>("/api/accounts") });
  const providers = useQuery({ queryKey: ["account-providers"], queryFn: () => api.get<ProviderInfo[]>("/api/accounts/providers") });
  const [form, setForm] = useState<Partial<Account> | null>(null);

  useEffect(() => {
    const o = params.get("oauth");
    if (!o) return;
    if (o === "ok") toast.success("OAuth authorized. Now run a connection test.");
    else toast.error(`OAuth ${o === "denied" ? "was cancelled" : "failed"}${params.get("reason") ? `: ${params.get("reason")}` : ""}`);
    setParams({});
    qc.invalidateQueries({ queryKey: ["accounts"] });
  }, [params, setParams, qc]);

  return (
    <>
      <PageHeader
        title="Sending accounts"
        description="Your own mailboxes. An account only becomes 'connected' after a real connection test succeeds, and sending stays off until you enable it."
        actions={
          <Button variant="primary" onClick={() => setForm({ provider: "smtp_imap", smtp_port: 587, smtp_security: "starttls", imap_port: 993, imap_security: "ssl", imap_folder: "INBOX", daily_limit: 30, hourly_limit: 10, min_delay_seconds: 120, imap_enabled: true })}>
            <Plus className="h-4 w-4" /> Add account
          </Button>
        }
      />
      {accounts.isLoading ? (
        <Spinner />
      ) : !accounts.data?.length ? (
        <Card>
          <EmptyState icon={<Send className="h-6 w-6" />} title="No sending accounts" description="Connect a mailbox you own via SMTP/IMAP (app password recommended) or OAuth for Gmail / Microsoft 365." />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {accounts.data.map((a) => (
            <AccountCard key={a.id} account={a} onEdit={() => setForm(a)} />
          ))}
        </div>
      )}
      <DeliverabilityGuide />
      {form && <AccountForm initial={form} providers={providers.data ?? []} onClose={() => setForm(null)} />}
    </>
  );
}

function AccountCard({ account: a, onEdit }: { account: Account; onEdit: () => void }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<"disconnect" | "delete" | null>(null);
  const [rotateOpen, setRotateOpen] = useState(false);
  const [dnsOpen, setDnsOpen] = useState(false);
  const [testResult, setTestResult] = useState<{ smtp: { ok: boolean; message: string } | null; imap: { ok: boolean; message: string } | null } | null>(null);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["accounts"] });
  const test = useMutation({
    mutationFn: () => api.post<{ result: typeof testResult }>(`/api/accounts/${a.id}/test`),
    onSuccess: (r) => {
      setTestResult(r.result);
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const sending = useMutation({
    mutationFn: (enabled: boolean) => api.post(`/api/accounts/${a.id}/sending`, { enabled }),
    onSuccess: invalidate,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const simple = useMutation({
    mutationFn: (path: string) => (path === "delete" ? api.delete(`/api/accounts/${a.id}`) : api.post(`/api/accounts/${a.id}/${path}`)),
    onSuccess: (_d, path) => {
      toast.success({ disconnect: "Disconnected; credentials deleted", delete: "Account deleted", sync: "Inbox sync queued for the worker" }[path] ?? "Done");
      setConfirm(null);
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const oauthStart = useMutation({
    mutationFn: () => api.post<{ authorize_url: string }>(`/api/accounts/${a.id}/oauth/start`),
    onSuccess: (r) => (window.location.href = r.authorize_url),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const isOauth = a.provider !== "smtp_imap";

  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-semibold">{a.display_name}</h3>
          <p className="truncate text-sm text-muted">{a.email}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <StatusBadge status={a.status} />
            <Badge>{a.provider === "smtp_imap" ? "SMTP/IMAP" : a.provider === "gmail_oauth" ? "Gmail OAuth" : "Microsoft OAuth"}</Badge>
            {a.imap_enabled && <Badge tone="info">inbox sync</Badge>}
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm">
          Sending
          <Switch label="Sending enabled" checked={a.sending_enabled} disabled={a.status !== "connected" && !a.sending_enabled} onChange={(v) => sending.mutate(v)} />
        </label>
      </div>
      {a.status_message && <p className="mt-3 text-xs text-muted">{a.status_message}</p>}
      <div className="mt-4">
        <KeyValue
          items={[
            ["SMTP", a.smtp_host ? `${a.smtp_host}:${a.smtp_port} (${a.smtp_security})` : "—"],
            ["IMAP", a.imap_enabled ? `${a.imap_host}:${a.imap_port} · ${a.imap_folder}` : "disabled"],
            ["Credentials", isOauth ? (a.has_oauth_token ? "OAuth token stored (encrypted)" : "Not authorized yet") : a.has_smtp_password ? "Password stored (encrypted)" : "None"],
            ["Limits", `${a.daily_limit}/day · ${a.hourly_limit}/hour · ≥${a.min_delay_seconds}s apart`],
            ["Sent last 24h", `${a.sent_last_24h} of ${a.daily_limit}`],
            ["Last test / send / sync", `${fmtDate(a.last_test_at)} · ${fmtDate(a.last_send_at)} · ${fmtDate(a.last_sync_at)}`],
          ]}
        />
      </div>
      {testResult && (
        <div className="mt-3 space-y-2">
          {testResult.smtp && <Alert tone={testResult.smtp.ok ? "ok" : "danger"}>{testResult.smtp.message}</Alert>}
          {testResult.imap && <Alert tone={testResult.imap.ok ? "ok" : "warn"}>{testResult.imap.message}</Alert>}
        </div>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        {isOauth && (
          <Button size="sm" variant={a.has_oauth_token ? "outline" : "primary"} onClick={() => oauthStart.mutate()} loading={oauthStart.isPending}>
            <Plug className="h-3.5 w-3.5" /> {a.has_oauth_token ? "Re-authorize" : "Connect with OAuth"}
          </Button>
        )}
        <Button size="sm" onClick={() => test.mutate()} loading={test.isPending}>
          <ShieldCheck className="h-3.5 w-3.5" /> Test connection
        </Button>
        {a.imap_enabled && (
          <Button size="sm" onClick={() => simple.mutate("sync")} disabled={a.status !== "connected"}>
            <RefreshCw className="h-3.5 w-3.5" /> Sync inbox
          </Button>
        )}
        <Button size="sm" onClick={() => setDnsOpen(true)}>
          DNS check
        </Button>
        <Button size="sm" variant="ghost" onClick={onEdit}>
          Edit
        </Button>
        {!isOauth && (
          <Button size="sm" variant="ghost" onClick={() => setRotateOpen(true)}>
            <KeyRound className="h-3.5 w-3.5" /> Rotate password
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={() => setConfirm("disconnect")} disabled={a.status === "disconnected"}>
          <Unplug className="h-3.5 w-3.5" /> Disconnect
        </Button>
        <Button size="icon" variant="ghost" aria-label="Delete account" onClick={() => setConfirm("delete")}>
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>
      <ConfirmDialog open={confirm === "disconnect"} onOpenChange={(v) => !v && setConfirm(null)} title="Disconnect account?" description="Stored credentials are deleted and sending is disabled. Campaigns using it will pause until it's reconnected." tone="danger" confirmLabel="Disconnect" loading={simple.isPending} onConfirm={() => simple.mutate("disconnect")} />
      <ConfirmDialog open={confirm === "delete"} onOpenChange={(v) => !v && setConfirm(null)} title="Delete account?" description="Only possible when no campaign uses it." tone="danger" confirmLabel="Delete" loading={simple.isPending} onConfirm={() => simple.mutate("delete")} />
      {rotateOpen && <RotateDialog account={a} onClose={() => setRotateOpen(false)} />}
      {dnsOpen && <DnsDialog account={a} onClose={() => setDnsOpen(false)} />}
    </Card>
  );
}

function AccountForm({ initial, providers, onClose }: { initial: Partial<Account>; providers: ProviderInfo[]; onClose: () => void }) {
  const qc = useQueryClient();
  const editing = !!initial.id;
  const [v, setV] = useState<Partial<Account> & { smtp_password?: string; imap_password?: string }>(initial);
  const set = (patch: Partial<typeof v>) => setV({ ...v, ...patch });
  const info = providers.find((p) => p.provider === v.provider);
  const save = useMutation({
    mutationFn: () => {
      const common = {
        display_name: v.display_name,
        reply_to: v.reply_to || null,
        daily_limit: Number(v.daily_limit),
        hourly_limit: Number(v.hourly_limit),
        min_delay_seconds: Number(v.min_delay_seconds),
      };
      const conn = {
        smtp_host: v.smtp_host,
        smtp_port: Number(v.smtp_port),
        smtp_security: v.smtp_security,
        smtp_username: v.smtp_username || null,
        imap_enabled: !!v.imap_enabled,
        imap_host: v.imap_host || null,
        imap_port: v.imap_port ? Number(v.imap_port) : null,
        imap_security: v.imap_security,
        imap_username: v.imap_username || null,
        imap_folder: v.imap_folder || "INBOX",
      };
      if (editing) return api.patch(`/api/accounts/${v.id}`, v.provider === "smtp_imap" ? { ...common, ...conn } : common);
      return api.post("/api/accounts", { ...common, ...(v.provider === "smtp_imap" ? conn : {}), email: v.email, provider: v.provider, smtp_password: v.smtp_password || null, imap_password: v.imap_password || null });
    },
    onSuccess: () => {
      toast.success(editing ? "Account updated" : "Account added — run a connection test next");
      qc.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const smtp = v.provider === "smtp_imap";
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={editing ? "Edit account" : "Add sending account"} size="lg"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending} disabled={!v.display_name || !v.email} onClick={() => save.mutate()}>{editing ? "Save" : "Add account"}</Button></>}>
      <div className="space-y-4">
        {!editing && (
          <Field label="Provider" htmlFor="ap">
            <Select id="ap" value={v.provider} onChange={(e) => set({ provider: e.target.value as Account["provider"] })}>
              {providers.map((p) => (
                <option key={p.provider} value={p.provider}>
                  {p.label}
                </option>
              ))}
            </Select>
          </Field>
        )}
        {info && (
          <Alert tone={info.oauth_configured === false ? "warn" : "info"} title="Permissions">
            <p>{info.permissions}</p>
            {info.scopes && <p className="mt-1 font-mono text-xs">{info.scopes.join("  ")}</p>}
            {info.oauth_configured === false && <p className="mt-1">OAuth client credentials aren't configured on the server yet (see README → OAuth).</p>}
          </Alert>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Display name" htmlFor="adn" hint="Shown to recipients — use your real name">
            <Input id="adn" value={v.display_name ?? ""} onChange={(e) => set({ display_name: e.target.value })} />
          </Field>
          <Field label="Sender email" htmlFor="aem">
            <Input id="aem" type="email" disabled={editing} value={v.email ?? ""} onChange={(e) => set({ email: e.target.value })} />
          </Field>
          <Field label="Reply-to (optional)" htmlFor="art">
            <Input id="art" type="email" value={v.reply_to ?? ""} onChange={(e) => set({ reply_to: e.target.value })} />
          </Field>
        </div>
        {smtp && (
          <>
            <fieldset className="grid grid-cols-1 gap-3 rounded-lg border border-border p-3 sm:grid-cols-4">
              <legend className="px-1 text-[13px] font-medium">SMTP (sending)</legend>
              <Field label="Host" htmlFor="sh" className="sm:col-span-2"><Input id="sh" value={v.smtp_host ?? ""} onChange={(e) => set({ smtp_host: e.target.value })} /></Field>
              <Field label="Port" htmlFor="sp"><Input id="sp" type="number" value={v.smtp_port ?? ""} onChange={(e) => set({ smtp_port: Number(e.target.value) })} /></Field>
              <Field label="Security" htmlFor="ss">
                <Select id="ss" value={v.smtp_security} onChange={(e) => set({ smtp_security: e.target.value })}>
                  <option value="starttls">STARTTLS</option>
                  <option value="ssl">SSL/TLS</option>
                  <option value="none">None (local only)</option>
                </Select>
              </Field>
              <Field label="Username" htmlFor="su" className="sm:col-span-2" hint="Defaults to the sender email"><Input id="su" value={v.smtp_username ?? ""} onChange={(e) => set({ smtp_username: e.target.value })} /></Field>
              {!editing && <Field label="Password / app password" htmlFor="spw" className="sm:col-span-2" hint="Encrypted at rest; never shown again"><Input id="spw" type="password" autoComplete="new-password" value={v.smtp_password ?? ""} onChange={(e) => set({ smtp_password: e.target.value })} /></Field>}
            </fieldset>
            <fieldset className="grid grid-cols-1 gap-3 rounded-lg border border-border p-3 sm:grid-cols-4">
              <legend className="px-1 text-[13px] font-medium">IMAP (reply & bounce detection)</legend>
              <label className="flex items-center gap-2 text-sm sm:col-span-4"><Checkbox checked={!!v.imap_enabled} onChange={(e) => set({ imap_enabled: e.target.checked })} /> Enable inbox sync (read-only)</label>
              {v.imap_enabled && (
                <>
                  <Field label="Host" htmlFor="ih" className="sm:col-span-2"><Input id="ih" value={v.imap_host ?? ""} onChange={(e) => set({ imap_host: e.target.value })} /></Field>
                  <Field label="Port" htmlFor="ip"><Input id="ip" type="number" value={v.imap_port ?? ""} onChange={(e) => set({ imap_port: Number(e.target.value) })} /></Field>
                  <Field label="Security" htmlFor="is">
                    <Select id="is" value={v.imap_security} onChange={(e) => set({ imap_security: e.target.value })}>
                      <option value="ssl">SSL/TLS</option>
                      <option value="starttls">STARTTLS</option>
                      <option value="none">None (local only)</option>
                    </Select>
                  </Field>
                  <Field label="Username" htmlFor="iu" className="sm:col-span-2"><Input id="iu" value={v.imap_username ?? ""} onChange={(e) => set({ imap_username: e.target.value })} /></Field>
                  <Field label="Folder" htmlFor="if"><Input id="if" value={v.imap_folder ?? "INBOX"} onChange={(e) => set({ imap_folder: e.target.value })} /></Field>
                  {!editing && <Field label="Password" htmlFor="ipw" hint="Blank = same as SMTP"><Input id="ipw" type="password" autoComplete="new-password" value={v.imap_password ?? ""} onChange={(e) => set({ imap_password: e.target.value })} /></Field>}
                </>
              )}
            </fieldset>
          </>
        )}
        <fieldset className="grid grid-cols-1 gap-3 rounded-lg border border-border p-3 sm:grid-cols-3">
          <legend className="px-1 text-[13px] font-medium">Sending limits for this account</legend>
          <Field label="Per day" htmlFor="ld"><Input id="ld" type="number" min={1} value={v.daily_limit ?? 30} onChange={(e) => set({ daily_limit: Number(e.target.value) })} /></Field>
          <Field label="Per hour" htmlFor="lh"><Input id="lh" type="number" min={1} value={v.hourly_limit ?? 10} onChange={(e) => set({ hourly_limit: Number(e.target.value) })} /></Field>
          <Field label="Min. seconds apart" htmlFor="lm"><Input id="lm" type="number" min={10} value={v.min_delay_seconds ?? 120} onChange={(e) => set({ min_delay_seconds: Number(e.target.value) })} /></Field>
          <p className="text-xs text-muted sm:col-span-3">Keep these at or below your provider's limits. New mailboxes should start low and increase gradually.</p>
        </fieldset>
        {editing && smtp && <p className="text-xs text-muted">Changing connection settings requires a new connection test. Use “Rotate password” to change credentials.</p>}
      </div>
    </Dialog>
  );
}

function RotateDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const qc = useQueryClient();
  const [smtp, setSmtp] = useState("");
  const [imap, setImap] = useState("");
  const save = useMutation({
    mutationFn: () => api.post(`/api/accounts/${account.id}/rotate`, { smtp_password: smtp || null, imap_password: imap || null }),
    onSuccess: () => {
      toast.success("Credentials rotated — run a connection test");
      qc.invalidateQueries({ queryKey: ["accounts"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Rotate credentials" size="sm"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!smtp && !imap} loading={save.isPending} onClick={() => save.mutate()}>Save</Button></>}>
      <div className="space-y-3">
        <Field label="New SMTP password" htmlFor="r1"><Input id="r1" type="password" autoComplete="new-password" value={smtp} onChange={(e) => setSmtp(e.target.value)} /></Field>
        {account.imap_enabled && <Field label="New IMAP password (optional)" htmlFor="r2"><Input id="r2" type="password" autoComplete="new-password" value={imap} onChange={(e) => setImap(e.target.value)} /></Field>}
      </div>
    </Dialog>
  );
}

interface DnsResult {
  domain: string;
  spf: { found: boolean; ok: boolean; records: string[]; message: string } | null;
  dmarc: { found: boolean; policy: string | null; records: string[] } | null;
  dkim: { selector: string; found: boolean; records: string[] } | null;
  notes: string[];
  error?: string;
}

function DnsDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const [selector, setSelector] = useState("");
  const check = useMutation({ mutationFn: () => api.get<DnsResult>(`/api/accounts/${account.id}/dns-check`, { selector }), onError: (e) => toast.error(errorMessage(e)) });
  const r = check.data;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`DNS check — ${account.email.split("@")[1]}`} size="lg">
      <div className="space-y-4">
        <div className="flex gap-2">
          <Input aria-label="DKIM selector" placeholder="DKIM selector (optional), e.g. google" value={selector} onChange={(e) => setSelector(e.target.value)} />
          <Button variant="primary" loading={check.isPending} onClick={() => check.mutate()}>Check</Button>
        </div>
        {r && (
          <div className="space-y-2">
            {r.error && <Alert tone="danger">{r.error}</Alert>}
            {r.spf && <Alert tone={r.spf.ok ? "ok" : "warn"} title={`SPF: ${r.spf.message}`}>{r.spf.records.map((x) => <code key={x} className="block break-all font-mono text-xs">{x}</code>)}</Alert>}
            {r.dmarc && <Alert tone={r.dmarc.found ? "ok" : "warn"} title={`DMARC: ${r.dmarc.found ? `policy p=${r.dmarc.policy}` : "no record"}`}>{r.dmarc.records.map((x) => <code key={x} className="block break-all font-mono text-xs">{x}</code>)}</Alert>}
            {r.dkim && <Alert tone={r.dkim.found ? "ok" : "warn"} title={`DKIM (${r.dkim.selector}): ${r.dkim.found ? "key found" : "not found"}`} />}
            {r.notes.map((n) => <p key={n} className="text-xs text-muted">{n}</p>)}
          </div>
        )}
      </div>
    </Dialog>
  );
}

function DeliverabilityGuide() {
  return (
    <Card className="mt-6 p-5">
      <h2 className="text-[15px] font-semibold">Authentication setup (SPF, DKIM, DMARC)</h2>
      <div className="mt-2 grid grid-cols-1 gap-4 text-sm text-ink-2 md:grid-cols-3">
        <div>
          <p className="font-medium text-ink">SPF</p>
          <p>Publish one TXT record on your domain listing who may send for it, e.g. <code className="font-mono text-xs">v=spf1 include:_spf.google.com ~all</code>. Use your provider's documented include.</p>
        </div>
        <div>
          <p className="font-medium text-ink">DKIM</p>
          <p>Enable DKIM signing in your provider's admin console and publish the TXT key it gives you at <code className="font-mono text-xs">selector._domainkey</code>.</p>
        </div>
        <div>
          <p className="font-medium text-ink">DMARC</p>
          <p>Start with monitoring: <code className="font-mono text-xs">_dmarc TXT "v=DMARC1; p=none; rua=mailto:you@yourdomain"</code>, then tighten to quarantine/reject.</p>
        </div>
      </div>
      <p className="mt-3 text-xs text-muted">These records help receivers verify your mail is really from you. They don't guarantee inbox placement — relevance, low volume and honoring opt-outs matter more.</p>
    </Card>
  );
}
