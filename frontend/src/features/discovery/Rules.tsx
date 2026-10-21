import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { Alert, Button, Card, CardBody, CardHeader, Checkbox, Field, Input, Select, Switch, Textarea } from "@/components/ui";
import { ConfirmDialog } from "@/components/ui/dialog";
import { api, ApiError, errorMessage } from "@/lib/api";
import { titleCase } from "@/lib/utils";
import type { DiscoveryConfig } from "@/types/api";

const LIST_FIELDS: [keyof DiscoveryConfig, string, string][] = [
  ["target_industries", "Target industries", "e.g. saas, retail, logistics"],
  ["company_sizes", "Company sizes", "Employee ranges like 11,50 or 51,200 (one per line)"],
  ["countries", "Countries", "e.g. india, united kingdom"],
  ["regions", "Regions / cities", "e.g. bengaluru, london"],
  ["buyer_roles", "Buyer roles", "e.g. founder, head of engineering, cto"],
  ["job_titles", "Job titles (Apollo search)", "Exact titles to search for"],
  ["keywords", "Keywords", "Words that make a signal relevant"],
  ["excluded_keywords", "Excluded keywords", "Signals containing these are dropped"],
  ["excluded_domains", "Excluded domains", "Customers, competitors, your own domain"],
  ["excluded_companies", "Excluded companies", "Company names to skip"],
  ["watch_domains", "Company websites to watch", "Domains whose careers/news pages are checked"],
  ["search_queries", "Search queries (Google CSE)", "Only used if you have legacy Custom Search access"],
];

const numbers = z.object({
  max_results_per_run: z.number().int().min(1).max(200),
  interval_minutes: z.number().int().min(15).max(10080),
  daily_limit: z.number().int().min(1).max(2000),
  min_relevance_score: z.number().min(0).max(100),
  review_threshold: z.number().min(0).max(100),
  ai_daily_limit: z.number().int().min(0).max(5000),
  ai_per_run_limit: z.number().int().min(0).max(500),
  enrichment_daily_credit_budget: z.number().int().min(0).max(10000),
  skip_if_contacted_within_days: z.number().int().min(0).max(3650),
}).refine((v) => v.review_threshold <= v.min_relevance_score, { message: "Review threshold must not exceed the minimum score", path: ["review_threshold"] });

const toText = (v: string[]) => v.join("\n");
const fromText = (s: string) => s.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

export function DiscoveryRules({ config, categories }: { config: DiscoveryConfig; categories: string[] }) {
  const qc = useQueryClient();
  const [v, setV] = useState<DiscoveryConfig>(config);
  const [lists, setLists] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<{ changes: Partial<DiscoveryConfig>; fields: string[] } | null>(null);
  useEffect(() => {
    setV(config);
    setLists(Object.fromEntries(LIST_FIELDS.map(([k]) => [k, toText(config[k] as string[])])));
  }, [config]);

  const build = (): Partial<DiscoveryConfig> => {
    const out: Record<string, unknown> = { ...v };
    for (const [k] of LIST_FIELDS) out[k] = fromText(lists[k] ?? "");
    delete out.enrichment_confirmed;
    return out as Partial<DiscoveryConfig>;
  };
  const save = useMutation({
    mutationFn: (p: { changes: Partial<DiscoveryConfig>; confirm?: boolean }) => api.patch("/api/discovery/config", { changes: p.changes, confirm: !!p.confirm }),
    onSuccess: () => {
      toast.success("Discovery rules saved");
      setPending(null);
      qc.invalidateQueries({ queryKey: ["discovery"] });
    },
    onError: (e, vars) => {
      if (e instanceof ApiError && e.code === "confirmation_required" && !vars.confirm) {
        setPending({ changes: vars.changes, fields: ((e.details as { fields?: string[] })?.fields) ?? [] });
      } else toast.error(errorMessage(e));
    },
  });
  function submit() {
    const res = numbers.safeParse(v);
    if (!res.success) {
      setErrors(Object.fromEntries(res.error.issues.map((i) => [String(i.path[0]), i.message])));
      return;
    }
    const bad = v.feeds.find((f) => !/^https?:\/\/\S+\.\S+/i.test(f.url));
    if (bad) {
      setErrors({ feeds: `Invalid feed URL: ${bad.url || "(empty)"}` });
      return;
    }
    setErrors({});
    save.mutate({ changes: build() });
  }
  const num = (k: keyof DiscoveryConfig, label: string, hint?: string) => (
    <Field label={label} htmlFor={`r-${k}`} error={errors[k as string]} hint={hint}>
      <Input id={`r-${k}`} type="number" value={String(v[k] ?? "")} onChange={(e) => setV({ ...v, [k]: Number(e.target.value) })} />
    </Field>
  );

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader title="Behaviour" />
        <CardBody className="space-y-3">
          <Toggle label="Test mode" hint="No credit-consuming calls and no external AI; drafts are written by the deterministic template provider and labelled test." checked={v.test_mode} onChange={(x) => setV({ ...v, test_mode: x })} />
          <Toggle label="Generate drafts for qualified signals" hint="Drafts always need your approval; nothing is sent." checked={v.draft_generation_enabled} onChange={(x) => setV({ ...v, draft_generation_enabled: x })} />
          <Toggle label="Apollo enrichment (uses credits)" hint="Reveals verified work emails for Apollo prospects. Capped by the daily credit budget below. Requires confirmation." checked={v.enrichment_enabled} onChange={(x) => setV({ ...v, enrichment_enabled: x })} />
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Targeting" description="One value per line (or comma separated). Empty lists don't affect the score." />
        <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {LIST_FIELDS.map(([k, label, hint]) => (
            <Field key={k} label={label} htmlFor={`l-${k}`} hint={hint}>
              <Textarea id={`l-${k}`} rows={3} value={lists[k] ?? ""} onChange={(e) => setLists({ ...lists, [k]: e.target.value })} />
            </Field>
          ))}
          <fieldset className="md:col-span-2">
            <legend className="mb-2 text-[13px] font-medium text-ink-2">Signal categories</legend>
            <div className="flex flex-wrap gap-3">
              {categories.map((c) => (
                <label key={c} className="flex items-center gap-1.5 text-sm">
                  <Checkbox checked={v.signal_categories.includes(c)} onChange={(e) => setV({ ...v, signal_categories: e.target.checked ? [...v.signal_categories, c] : v.signal_categories.filter((x) => x !== c) })} />
                  {titleCase(c)}
                </label>
              ))}
            </div>
          </fieldset>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Feeds" description="RSS/Atom feeds from company blogs, newsrooms or job boards that publish feeds." actions={<Button size="sm" onClick={() => setV({ ...v, feeds: [...v.feeds, { url: "", category: null, company_name: null, company_domain: null }] })}><Plus className="h-3.5 w-3.5" /> Add feed</Button>} />
        <CardBody className="space-y-2">
          {errors.feeds && <Alert tone="danger">{errors.feeds}</Alert>}
          {!v.feeds.length && <p className="text-sm text-muted">No feeds yet.</p>}
          {v.feeds.map((f, i) => (
            <div key={i} className="grid grid-cols-1 gap-2 rounded-lg border border-border p-2 md:grid-cols-[2fr_1fr_1fr_1fr_auto]">
              <Input aria-label="Feed URL" placeholder="https://example.com/feed.xml" value={f.url} onChange={(e) => setV({ ...v, feeds: v.feeds.map((x, j) => (j === i ? { ...x, url: e.target.value } : x)) })} />
              <Input aria-label="Company name" placeholder="Company (optional)" value={f.company_name ?? ""} onChange={(e) => setV({ ...v, feeds: v.feeds.map((x, j) => (j === i ? { ...x, company_name: e.target.value || null } : x)) })} />
              <Input aria-label="Company domain" placeholder="domain (optional)" value={f.company_domain ?? ""} onChange={(e) => setV({ ...v, feeds: v.feeds.map((x, j) => (j === i ? { ...x, company_domain: e.target.value || null } : x)) })} />
              <Select aria-label="Feed category" value={f.category ?? ""} onChange={(e) => setV({ ...v, feeds: v.feeds.map((x, j) => (j === i ? { ...x, category: e.target.value || null } : x)) })}>
                <option value="">Auto category</option>
                {categories.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
              </Select>
              <Button size="icon" variant="ghost" aria-label="Remove feed" onClick={() => setV({ ...v, feeds: v.feeds.filter((_, j) => j !== i) })}><Trash2 className="h-4 w-4" /></Button>
            </div>
          ))}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Thresholds & limits" />
        <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {num("min_relevance_score", "Minimum score to draft (0–100)")}
          {num("review_threshold", "Review threshold", "Between this and the minimum: kept for your review, no draft")}
          {num("skip_if_contacted_within_days", "Skip leads contacted within (days)")}
          {num("interval_minutes", "Run every (minutes)", "15 minutes minimum")}
          {num("max_results_per_run", "Max new signals per run")}
          {num("daily_limit", "Max new signals per day")}
          {num("ai_per_run_limit", "Max AI drafts per run")}
          {num("ai_daily_limit", "Max AI drafts per day")}
          {num("enrichment_daily_credit_budget", "Apollo credit budget per day", "Counted conservatively as 1 credit per enrichment call")}
          {num("max_pages_per_domain", "Max pages per company site")}
          {num("max_evidence_chars", "Max evidence characters sent to AI")}
          {num("evidence_retention_days", "Keep rejected/archived evidence (days, 0 = forever)")}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Your offer" description="Used by drafting. Only write what is true about Claxon AI." />
        <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Field label="Product or service" htmlFor="o-p"><Textarea id="o-p" rows={2} value={v.offer.product_description} onChange={(e) => setV({ ...v, offer: { ...v.offer, product_description: e.target.value } })} /></Field>
          <Field label="Value proposition" htmlFor="o-v"><Textarea id="o-v" rows={2} value={v.offer.value_proposition} onChange={(e) => setV({ ...v, offer: { ...v.offer, value_proposition: e.target.value } })} /></Field>
          <Field label="Call to action" htmlFor="o-c"><Input id="o-c" value={v.offer.call_to_action} onChange={(e) => setV({ ...v, offer: { ...v.offer, call_to_action: e.target.value } })} /></Field>
          <Field label="Target audience" htmlFor="o-a"><Input id="o-a" value={v.offer.target_audience} onChange={(e) => setV({ ...v, offer: { ...v.offer, target_audience: e.target.value } })} /></Field>
          <Field label="Tone" htmlFor="o-t">
            <Select id="o-t" value={v.offer.tone} onChange={(e) => setV({ ...v, offer: { ...v.offer, tone: e.target.value } })}>
              {["professional", "friendly", "direct", "casual"].map((t) => <option key={t}>{t}</option>)}
            </Select>
          </Field>
          <Field label="Length" htmlFor="o-l">
            <Select id="o-l" value={v.offer.length} onChange={(e) => setV({ ...v, offer: { ...v.offer, length: e.target.value } })}>
              {["short", "medium", "long"].map((t) => <option key={t}>{t}</option>)}
            </Select>
          </Field>
        </CardBody>
      </Card>
      <div className="sticky bottom-0 flex justify-end gap-2 border-t border-border bg-bg/90 py-3 backdrop-blur">
        <Button onClick={() => setV(config)}>Discard</Button>
        <Button variant="primary" loading={save.isPending} onClick={submit}>Save rules</Button>
      </div>
      <ConfirmDialog
        open={!!pending}
        onOpenChange={(o) => !o && setPending(null)}
        title="Confirm"
        description={pending?.fields.includes("enrichment_enabled") ? `Apollo enrichment consumes credits (at least 1 per person). Up to ${v.enrichment_daily_credit_budget} enrichment call(s) per day will be allowed.` : "Scheduled discovery will run automatically within your limits. Nothing is sent."}
        confirmLabel="Confirm"
        tone={pending?.fields.includes("enrichment_enabled") ? "danger" : "primary"}
        loading={save.isPending}
        onConfirm={() => pending && save.mutate({ changes: pending.changes, confirm: true })}
      />
    </div>
  );
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border border-border p-3">
      <div>
        <p className="text-sm font-medium">{label}</p>
        <p className="text-xs text-muted">{hint}</p>
      </div>
      <Switch label={label} checked={checked} onChange={onChange} />
    </div>
  );
}
