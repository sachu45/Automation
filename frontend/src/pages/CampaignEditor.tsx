import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { useEffect } from "react";
import { Controller, useForm } from "react-hook-form";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";
import { useAppSettings } from "@/app/Layout";
import { Alert, Button, Card, CardBody, CardHeader, Checkbox, Field, Input, PageHeader, Select, Spinner, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { browserTimezone, cn, WEEKDAYS } from "@/lib/utils";
import type { Account, Campaign, LeadList, Segment, Sequence } from "@/types/api";

const hhmm = z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/, "Use HH:MM");
const schema = z
  .object({
    name: z.string().trim().min(1, "Name is required").max(200),
    description: z.string().max(5000).optional(),
    email_account_id: z.coerce.number().min(1, "Choose a sending account"),
    sequence_id: z.coerce.number().min(1, "Choose a sequence"),
    list_ids: z.array(z.number()),
    segment_ids: z.array(z.number()),
    timezone: z.string().min(1),
    window_start: hhmm,
    window_end: hhmm,
    send_days: z.array(z.number()).min(1, "Pick at least one day"),
    daily_limit: z.coerce.number().int().min(1).max(2000),
    min_delay_seconds: z.coerce.number().int().min(10).max(86400),
    max_emails_per_recipient: z.coerce.number().int().min(1).max(10),
    start_at: z.string().optional(),
    end_at: z.string().optional(),
    exclude_domains: z.string().optional(),
    skip_if_contacted_within_days: z.string().optional(),
    exclude_active_in_other_campaigns: z.boolean(),
  })
  .refine((v) => v.list_ids.length + v.segment_ids.length > 0, { message: "Choose at least one list or segment", path: ["list_ids"] })
  .refine((v) => v.window_start < v.window_end, { message: "Window start must be before end", path: ["window_end"] });

type Values = z.infer<typeof schema>;

const toLocalInput = (iso: string | null) => {
  if (!iso) return "";
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
};

export function CampaignEditorPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const settings = useAppSettings();
  const existing = useQuery({ queryKey: ["campaign", id], queryFn: () => api.get<Campaign>(`/api/campaigns/${id}`), enabled: !!id });
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: () => api.get<Account[]>("/api/accounts") });
  const sequences = useQuery({ queryKey: ["sequences"], queryFn: () => api.get<Sequence[]>("/api/sequences") });
  const lists = useQuery({ queryKey: ["lists"], queryFn: () => api.get<LeadList[]>("/api/lists") });
  const segments = useQuery({ queryKey: ["segments"], queryFn: () => api.get<Segment[]>("/api/segments") });

  const cfg = settings.data?.config;
  const { register, handleSubmit, control, reset, formState } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: "",
      description: "",
      email_account_id: 0,
      sequence_id: 0,
      list_ids: [],
      segment_ids: [],
      timezone: browserTimezone(),
      window_start: "09:00",
      window_end: "17:00",
      send_days: [0, 1, 2, 3, 4],
      daily_limit: 25,
      min_delay_seconds: 180,
      max_emails_per_recipient: 4,
      exclude_active_in_other_campaigns: true,
    },
  });

  useEffect(() => {
    if (!id && cfg) reset((v) => ({ ...v, timezone: cfg.default_timezone === "UTC" ? browserTimezone() : cfg.default_timezone, window_start: cfg.default_window_start, window_end: cfg.default_window_end, send_days: cfg.default_send_days, min_delay_seconds: Math.max(cfg.min_delay_seconds, 60) }));
  }, [cfg, id, reset]);

  useEffect(() => {
    const c = existing.data;
    if (!c) return;
    reset({
      name: c.name,
      description: c.description ?? "",
      email_account_id: c.email_account_id ?? 0,
      sequence_id: c.sequence_id ?? 0,
      list_ids: c.list_ids,
      segment_ids: c.segment_ids,
      timezone: c.timezone,
      window_start: c.window_start,
      window_end: c.window_end,
      send_days: c.send_days,
      daily_limit: c.daily_limit,
      min_delay_seconds: c.min_delay_seconds,
      max_emails_per_recipient: c.max_emails_per_recipient,
      start_at: toLocalInput(c.start_at),
      end_at: toLocalInput(c.end_at),
      exclude_domains: (c.exclusion_rules.exclude_domains ?? []).join(", "),
      skip_if_contacted_within_days: c.exclusion_rules.skip_if_contacted_within_days ? String(c.exclusion_rules.skip_if_contacted_within_days) : "",
      exclude_active_in_other_campaigns: c.exclusion_rules.exclude_active_in_other_campaigns ?? true,
    });
  }, [existing.data, reset]);

  const save = useMutation({
    mutationFn: (v: Values) => {
      const body = {
        name: v.name,
        description: v.description || null,
        email_account_id: v.email_account_id,
        sequence_id: v.sequence_id,
        list_ids: v.list_ids,
        segment_ids: v.segment_ids,
        timezone: v.timezone,
        window_start: v.window_start,
        window_end: v.window_end,
        send_days: v.send_days,
        daily_limit: v.daily_limit,
        min_delay_seconds: v.min_delay_seconds,
        max_emails_per_recipient: v.max_emails_per_recipient,
        start_at: v.start_at ? new Date(v.start_at).toISOString() : null,
        end_at: v.end_at ? new Date(v.end_at).toISOString() : null,
        exclusion_rules: {
          exclude_domains: (v.exclude_domains ?? "").split(",").map((d) => d.trim()).filter(Boolean),
          skip_if_contacted_within_days: v.skip_if_contacted_within_days ? Number(v.skip_if_contacted_within_days) : null,
          exclude_active_in_other_campaigns: v.exclude_active_in_other_campaigns,
        },
      };
      return id ? api.put<Campaign>(`/api/campaigns/${id}`, body) : api.post<Campaign>("/api/campaigns", body);
    },
    onSuccess: (c) => {
      toast.success("Campaign saved as draft");
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      qc.invalidateQueries({ queryKey: ["campaign", String(c.id)] });
      navigate(`/campaigns/${c.id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (id && existing.isLoading) return <Spinner />;
  const e = formState.errors;
  const notEditable = existing.data && !["draft", "ready_for_review", "approved"].includes(existing.data.status);

  return (
    <>
      <Link to={id ? `/campaigns/${id}` : "/campaigns"} className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
        <ArrowLeft className="h-4 w-4" /> Back
      </Link>
      <PageHeader title={id ? "Edit campaign" : "New campaign"} description="Saving keeps the campaign as a draft. Nothing sends until you review, approve and activate it." />
      {notEditable && <Alert tone="warn" className="mb-4">This campaign has started; only operational settings can be changed from its detail page.</Alert>}
      {existing.data && existing.data.status !== "draft" && !notEditable && (
        <Alert tone="info" className="mb-4">Saving changes returns this campaign to draft and clears its approval.</Alert>
      )}
      <form onSubmit={handleSubmit((v) => save.mutate(v))} className="space-y-6" noValidate>
        <Card>
          <CardHeader title="Basics" />
          <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Field label="Campaign name" htmlFor="cn" error={e.name?.message}>
              <Input id="cn" {...register("name")} />
            </Field>
            <Field label="Sending account" htmlFor="ca" error={e.email_account_id?.message} hint={accounts.data?.length === 0 ? <Link className="text-brand" to="/accounts">Add an account first</Link> : undefined}>
              <Select id="ca" {...register("email_account_id")}>
                <option value={0}>Choose…</option>
                {accounts.data?.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.display_name} &lt;{a.email}&gt; ({a.status})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Sequence" htmlFor="cs" error={e.sequence_id?.message} hint={sequences.data?.length === 0 ? <Link className="text-brand" to="/sequences">Create a sequence first</Link> : "Step 1's template provides the subject line"}>
              <Select id="cs" {...register("sequence_id")}>
                <option value={0}>Choose…</option>
                {sequences.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} ({s.steps.length} steps)
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Internal description" htmlFor="cd">
              <Textarea id="cd" rows={2} {...register("description")} />
            </Field>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Recipients" description="Leads in several selected lists/segments are included only once." />
          <CardBody className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <Controller
              control={control}
              name="list_ids"
              render={({ field }) => (
                <fieldset>
                  <legend className="mb-2 text-[13px] font-medium text-ink-2">Lists</legend>
                  <div className="max-h-56 space-y-1.5 overflow-y-auto">
                    {lists.data?.map((l) => (
                      <label key={l.id} className="flex items-center gap-2 text-sm">
                        <Checkbox checked={field.value.includes(l.id)} onChange={(ev) => field.onChange(ev.target.checked ? [...field.value, l.id] : field.value.filter((x) => x !== l.id))} />
                        {l.name} <span className="text-xs text-muted">({l.lead_count})</span>
                      </label>
                    ))}
                    {!lists.data?.length && <p className="text-sm text-muted">No lists yet.</p>}
                  </div>
                  {e.list_ids && <p className="mt-1 text-xs text-danger">{e.list_ids.message}</p>}
                </fieldset>
              )}
            />
            <Controller
              control={control}
              name="segment_ids"
              render={({ field }) => (
                <fieldset>
                  <legend className="mb-2 text-[13px] font-medium text-ink-2">Segments</legend>
                  <div className="max-h-56 space-y-1.5 overflow-y-auto">
                    {segments.data?.map((s) => (
                      <label key={s.id} className="flex items-center gap-2 text-sm">
                        <Checkbox checked={field.value.includes(s.id)} onChange={(ev) => field.onChange(ev.target.checked ? [...field.value, s.id] : field.value.filter((x) => x !== s.id))} />
                        {s.name} <span className="text-xs text-muted">({s.lead_count})</span>
                      </label>
                    ))}
                    {!segments.data?.length && <p className="text-sm text-muted">No segments yet.</p>}
                  </div>
                </fieldset>
              )}
            />
            <Field label="Exclude domains" htmlFor="ced" hint="Comma separated, e.g. competitor.com">
              <Input id="ced" {...register("exclude_domains")} />
            </Field>
            <Field label="Skip leads contacted in the last N days" htmlFor="cskip" hint="Leave blank to disable">
              <Input id="cskip" type="number" min={1} {...register("skip_if_contacted_within_days")} />
            </Field>
            <label className="flex items-center gap-2 text-sm md:col-span-2">
              <Checkbox {...register("exclude_active_in_other_campaigns")} /> Skip leads already active in another campaign
            </label>
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Schedule & limits" description="Times are interpreted in the campaign timezone (DST-aware). Account and global limits also apply — the strictest wins." />
          <CardBody className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Field label="Timezone" htmlFor="ctz" hint="IANA name">
              <Input id="ctz" {...register("timezone")} />
            </Field>
            <Field label="Window start" htmlFor="cws" error={e.window_start?.message}>
              <Input id="cws" type="time" {...register("window_start")} />
            </Field>
            <Field label="Window end" htmlFor="cwe" error={e.window_end?.message}>
              <Input id="cwe" type="time" {...register("window_end")} />
            </Field>
            <Controller
              control={control}
              name="send_days"
              render={({ field }) => (
                <fieldset className="md:col-span-3">
                  <legend className="mb-2 text-[13px] font-medium text-ink-2">Sending days</legend>
                  <div className="flex flex-wrap gap-1.5">
                    {WEEKDAYS.map((d, i) => {
                      const on = field.value.includes(i);
                      return (
                        <button
                          key={d}
                          type="button"
                          aria-pressed={on}
                          onClick={() => field.onChange(on ? field.value.filter((x) => x !== i) : [...field.value, i].sort())}
                          className={cn("rounded-lg px-3 py-1.5 text-sm ring-1", on ? "bg-brand text-white ring-brand" : "bg-surface text-ink-2 ring-border-strong")}
                        >
                          {d}
                        </button>
                      );
                    })}
                  </div>
                  {e.send_days && <p className="mt-1 text-xs text-danger">{e.send_days.message}</p>}
                </fieldset>
              )}
            />
            <Field label="Daily limit (this campaign)" htmlFor="cdl" error={e.daily_limit?.message}>
              <Input id="cdl" type="number" min={1} {...register("daily_limit")} />
            </Field>
            <Field label="Min. seconds between emails" htmlFor="cmd" error={e.min_delay_seconds?.message}>
              <Input id="cmd" type="number" min={10} {...register("min_delay_seconds")} />
            </Field>
            <Field label="Max emails per recipient" htmlFor="cmx" error={e.max_emails_per_recipient?.message}>
              <Input id="cmx" type="number" min={1} max={10} {...register("max_emails_per_recipient")} />
            </Field>
            <Field label="Start (optional)" htmlFor="cst" hint="Your local time">
              <Input id="cst" type="datetime-local" {...register("start_at")} />
            </Field>
            <Field label="End (optional)" htmlFor="cen" hint="Unsent steps stop after this">
              <Input id="cen" type="datetime-local" {...register("end_at")} />
            </Field>
          </CardBody>
        </Card>

        <div className="flex justify-end gap-2">
          <Link to={id ? `/campaigns/${id}` : "/campaigns"}>
            <Button>Cancel</Button>
          </Link>
          <Button type="submit" variant="primary" loading={save.isPending} disabled={!!notEditable}>
            Save draft
          </Button>
        </div>
      </form>
    </>
  );
}
