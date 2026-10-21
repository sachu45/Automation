import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, Field, Input, Select, Textarea } from "@/components/ui";
import type { Lead } from "@/types/api";

const url = z
  .string()
  .trim()
  .max(500)
  .optional()
  .refine((v) => !v || /^(https?:\/\/)?[^\s]+\.[^\s]+$/i.test(v), "Enter a valid URL");

export const leadSchema = z.object({
  email: z.string().trim().min(1, "Email is required").email("Enter a valid email address"),
  first_name: z.string().trim().max(120).optional(),
  last_name: z.string().trim().max(120).optional(),
  job_title: z.string().trim().max(255).optional(),
  company: z.string().trim().max(255).optional(),
  company_website: url,
  linkedin_url: url.refine((v) => !v || v.toLowerCase().includes("linkedin.com/"), "Must be a linkedin.com link"),
  industry: z.string().trim().max(120).optional(),
  country: z.string().trim().max(80).optional(),
  timezone: z.string().trim().max(64).optional(),
  source: z.string().trim().max(120).optional(),
  status: z.enum(["new", "ready", "contacted", "replied", "interested", "not_interested", "do_not_contact"]).optional(),
  tags: z.string().optional(),
  notes: z.string().max(5000).optional(),
  custom: z.string().optional(),
});

export type LeadFormValues = z.infer<typeof leadSchema>;

export function parseCustom(text?: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of (text ?? "").split("\n")) {
    const idx = line.indexOf("=");
    if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
  }
  return out;
}

export function toPayload(v: LeadFormValues) {
  const blank = (s?: string) => (s && s.trim() ? s.trim() : null);
  return {
    email: v.email.trim(),
    first_name: blank(v.first_name),
    last_name: blank(v.last_name),
    job_title: blank(v.job_title),
    company: blank(v.company),
    company_website: blank(v.company_website),
    linkedin_url: blank(v.linkedin_url),
    industry: blank(v.industry),
    country: blank(v.country),
    timezone: blank(v.timezone),
    source: blank(v.source),
    notes: blank(v.notes),
    tags: (v.tags ?? "")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean),
    custom_fields: parseCustom(v.custom),
    ...(v.status ? { status: v.status } : {}),
  };
}

export function leadDefaults(lead?: Lead): LeadFormValues {
  return {
    email: lead?.email ?? "",
    first_name: lead?.first_name ?? "",
    last_name: lead?.last_name ?? "",
    job_title: lead?.job_title ?? "",
    company: lead?.company ?? "",
    company_website: lead?.company_website ?? "",
    linkedin_url: lead?.linkedin_url ?? "",
    industry: lead?.industry ?? "",
    country: lead?.country ?? "",
    timezone: lead?.timezone ?? "",
    source: lead?.source ?? "",
    status: (lead?.status as LeadFormValues["status"]) ?? "new",
    tags: lead?.tags.join(", ") ?? "",
    notes: lead?.notes ?? "",
    custom: Object.entries(lead?.custom_fields ?? {})
      .map(([k, v]) => `${k}=${v ?? ""}`)
      .join("\n"),
  };
}

export function LeadForm({ lead, onSubmit, submitting, onCancel }: { lead?: Lead; onSubmit: (v: LeadFormValues) => void; submitting?: boolean; onCancel?: () => void }) {
  const { register, handleSubmit, formState } = useForm<LeadFormValues>({ resolver: zodResolver(leadSchema), defaultValues: leadDefaults(lead) });
  const e = formState.errors;
  const lockedStatus = lead && ["bounced", "unsubscribed"].includes(lead.status);
  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Email *" htmlFor="email" error={e.email?.message} className="sm:col-span-2">
          <Input id="email" type="email" {...register("email")} aria-invalid={!!e.email} />
        </Field>
        <Field label="First name" htmlFor="first_name">
          <Input id="first_name" {...register("first_name")} />
        </Field>
        <Field label="Last name" htmlFor="last_name">
          <Input id="last_name" {...register("last_name")} />
        </Field>
        <Field label="Job title" htmlFor="job_title">
          <Input id="job_title" {...register("job_title")} />
        </Field>
        <Field label="Company" htmlFor="company">
          <Input id="company" {...register("company")} />
        </Field>
        <Field label="Company website" htmlFor="company_website" error={e.company_website?.message}>
          <Input id="company_website" placeholder="example.com" {...register("company_website")} />
        </Field>
        <Field label="LinkedIn URL" htmlFor="linkedin_url" error={e.linkedin_url?.message} hint="Only if supplied legitimately">
          <Input id="linkedin_url" {...register("linkedin_url")} />
        </Field>
        <Field label="Industry" htmlFor="industry">
          <Input id="industry" {...register("industry")} />
        </Field>
        <Field label="Country" htmlFor="country">
          <Input id="country" {...register("country")} />
        </Field>
        <Field label="Timezone" htmlFor="timezone" hint="IANA name, e.g. Europe/Berlin">
          <Input id="timezone" {...register("timezone")} />
        </Field>
        <Field label="Lead source" htmlFor="source">
          <Input id="source" {...register("source")} />
        </Field>
        <Field label="Status" htmlFor="status" hint={lockedStatus ? "Managed by the suppression list" : undefined}>
          <Select id="status" {...register("status")} disabled={!!lockedStatus}>
            {["new", "ready", "contacted", "replied", "interested", "not_interested", "do_not_contact"].map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Tags" htmlFor="tags" hint="Comma separated">
          <Input id="tags" {...register("tags")} />
        </Field>
        <Field label="Personalization notes" htmlFor="notes" hint="Facts you verified yourself. AI drafts may only use what's here." className="sm:col-span-2">
          <Textarea id="notes" rows={3} {...register("notes")} />
        </Field>
        <Field label="Custom fields" htmlFor="custom" hint="One per line: key=value. Use in templates as {{custom.key}}." className="sm:col-span-2">
          <Textarea id="custom" rows={3} className="font-mono text-[13px]" {...register("custom")} />
        </Field>
      </div>
      <div className="flex justify-end gap-2 pt-1">
        {onCancel && <Button onClick={onCancel}>Cancel</Button>}
        <Button type="submit" variant="primary" loading={submitting}>
          {lead ? "Save changes" : "Create lead"}
        </Button>
      </div>
    </form>
  );
}
