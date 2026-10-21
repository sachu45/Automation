import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn, titleCase } from "@/lib/utils";

/* ---------------------------------- Button --------------------------------- */

type Variant = "primary" | "secondary" | "ghost" | "danger" | "outline";
type Size = "sm" | "md" | "icon";

const variants: Record<Variant, string> = {
  primary: "bg-brand text-white hover:bg-brand-hover shadow-sm",
  secondary: "bg-ink text-white hover:bg-ink-2 shadow-sm",
  outline: "border border-border-strong bg-surface text-ink hover:bg-bg",
  ghost: "text-ink-2 hover:bg-bg",
  danger: "bg-danger text-white hover:opacity-90 shadow-sm",
};
const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-[13px] gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
  icon: "h-8 w-8 justify-center",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "outline", size = "md", loading, disabled, children, type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      className={cn(
        "inline-flex shrink-0 items-center rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
});

/* ---------------------------------- Inputs --------------------------------- */

const fieldBase =
  "w-full rounded-lg border border-border-strong bg-surface px-3 text-sm text-ink placeholder:text-faint shadow-[0_1px_1px_rgba(16,24,40,0.03)] focus:border-brand focus:outline-none focus:ring-3 focus:ring-brand/15 disabled:bg-bg disabled:text-muted";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...p }, ref) {
  return <input ref={ref} className={cn(fieldBase, "h-9", className)} {...p} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...p },
  ref,
) {
  return <textarea ref={ref} className={cn(fieldBase, "min-h-[90px] py-2 leading-relaxed", className)} {...p} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, ...p }, ref) {
  return <select ref={ref} className={cn(fieldBase, "h-9 pr-8", className)} {...p} />;
});

export function Label({ className, ...p }: HTMLAttributes<HTMLLabelElement> & { htmlFor?: string }) {
  return <label className={cn("text-[13px] font-medium text-ink-2", className)} {...p} />;
}

export function Field({
  label,
  htmlFor,
  error,
  hint,
  children,
  className,
}: {
  label: ReactNode;
  htmlFor?: string;
  error?: string;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {error ? (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-muted">{hint}</p>
      ) : null}
    </div>
  );
}

export function Checkbox({ className, ...p }: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="checkbox" className={cn("h-4 w-4 rounded border-border-strong accent-brand", className)} {...p} />;
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50",
        checked ? "bg-brand" : "bg-border-strong",
      )}
    >
      <span className={cn("inline-block h-4 w-4 rounded-full bg-white shadow transition-transform", checked ? "translate-x-[18px]" : "translate-x-0.5")} />
    </button>
  );
}

/* ----------------------------------- Card ---------------------------------- */

export function Card({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-[var(--radius-card)] border border-border bg-surface shadow-[var(--shadow-card)]", className)} {...p} />;
}

export function CardHeader({ title, description, actions, className }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4", className)}>
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
        {description && <p className="mt-0.5 text-[13px] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function CardBody({ className, ...p }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5", className)} {...p} />;
}

/* ---------------------------------- Badge ---------------------------------- */

type Tone = "neutral" | "ok" | "warn" | "danger" | "info" | "brand";
const tones: Record<Tone, string> = {
  neutral: "bg-bg text-ink-2 ring-border",
  ok: "bg-ok-soft text-ok ring-ok/20",
  warn: "bg-warn-soft text-warn ring-warn/20",
  danger: "bg-danger-soft text-danger ring-danger/20",
  info: "bg-info-soft text-info ring-info/20",
  brand: "bg-brand-soft text-brand ring-brand/20",
};

export function Badge({ tone = "neutral", className, children }: { tone?: Tone; className?: string; children: ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset", tones[tone], className)}>
      {children}
    </span>
  );
}

const STATUS_TONES: Record<string, Tone> = {
  // campaign
  draft: "neutral",
  ready_for_review: "info",
  approved: "brand",
  scheduled: "info",
  active: "ok",
  paused: "warn",
  completed: "neutral",
  cancelled: "danger",
  // leads / recipients
  new: "neutral",
  ready: "info",
  contacted: "brand",
  replied: "ok",
  interested: "ok",
  not_interested: "neutral",
  bounced: "danger",
  unsubscribed: "danger",
  do_not_contact: "danger",
  pending: "neutral",
  suppressed: "danger",
  failed: "danger",
  stopped: "neutral",
  needs_review: "warn",
  // accounts
  connected: "ok",
  unverified: "warn",
  error: "danger",
  auth_failed: "danger",
  disconnected: "neutral",
  // attempts
  accepted: "ok",
  simulated: "info",
  sending: "info",
  blocked: "warn",
  failed_temporary: "warn",
  failed_permanent: "danger",
  unknown: "warn",
  delivered: "ok",
  // reply labels
  referral: "ok",
  question: "info",
  out_of_office: "neutral",
  unsubscribe: "danger",
  automated: "neutral",
  open: "info",
  handled: "neutral",
};

export function StatusBadge({ status, label }: { status: string | null | undefined; label?: string }) {
  if (!status) return <span className="text-faint">—</span>;
  return <Badge tone={STATUS_TONES[status] ?? "neutral"}>{label ?? titleCase(status)}</Badge>;
}

/* ---------------------------------- Table ---------------------------------- */

export function Table({ className, ...p }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="scroll-thin w-full overflow-x-auto">
      <table className={cn("w-full border-collapse text-sm", className)} {...p} />
    </div>
  );
}
export function Th({ className, ...p }: HTMLAttributes<HTMLTableCellElement>) {
  return <th className={cn("whitespace-nowrap border-b border-border bg-bg/60 px-4 py-2.5 text-left text-xs font-medium text-muted", className)} {...p} />;
}
export function Td({ className, ...p }: HTMLAttributes<HTMLTableCellElement> & { colSpan?: number }) {
  return <td className={cn("border-b border-border px-4 py-3 align-middle text-ink-2", className)} {...p} />;
}

/* --------------------------------- Feedback -------------------------------- */

export function Spinner({ className, label = "Loading" }: { className?: string; label?: string }) {
  return (
    <div role="status" className={cn("flex items-center justify-center gap-2 py-10 text-sm text-muted", className)}>
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      {label}…
    </div>
  );
}

export function EmptyState({ icon, title, description, action }: { icon?: ReactNode; title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon && <div className="mb-3 rounded-xl bg-bg p-3 text-muted">{icon}</div>}
      <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
      {description && <p className="mt-1 max-w-md text-sm text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

const alertIcons = { info: Info, ok: CheckCircle2, warn: AlertTriangle, danger: XCircle };
export function Alert({ tone = "info", title, children, className }: { tone?: keyof typeof alertIcons; title?: ReactNode; children?: ReactNode; className?: string }) {
  const Icon = alertIcons[tone];
  const toneCls = { info: "bg-info-soft text-info border-info/20", ok: "bg-ok-soft text-ok border-ok/20", warn: "bg-warn-soft text-warn border-warn/25", danger: "bg-danger-soft text-danger border-danger/20" }[tone];
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cn("flex gap-3 rounded-lg border px-3.5 py-3 text-sm", toneCls, className)}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="min-w-0 text-ink-2">
        {title && <p className="font-medium text-ink">{title}</p>}
        {children && <div className={cn(title && "mt-0.5")}>{children}</div>}
      </div>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : "Something went wrong";
  return (
    <div className="p-5">
      <Alert tone="danger" title="Couldn't load this">
        <p>{msg}</p>
        {onRetry && (
          <Button size="sm" className="mt-2" onClick={onRetry}>
            Try again
          </Button>
        )}
      </Alert>
    </div>
  );
}

/* ---------------------------------- Layout --------------------------------- */

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-ink sm:text-[22px]">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, tabs }: { value: T; onChange: (v: T) => void; tabs: { value: T; label: ReactNode }[] }) {
  return (
    <div role="tablist" className="mb-5 flex gap-1 overflow-x-auto border-b border-border">
      {tabs.map((t) => (
        <button
          key={t.value}
          role="tab"
          aria-selected={value === t.value}
          onClick={() => onChange(t.value)}
          className={cn(
            "-mb-px whitespace-nowrap border-b-2 px-3 pb-2.5 pt-1 text-sm font-medium transition-colors",
            value === t.value ? "border-brand text-ink" : "border-transparent text-muted hover:text-ink",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-[13px] text-muted">
      <span className="tabular">
        {from}–{to} of {total.toLocaleString()}
      </span>
      <div className="flex items-center gap-2">
        <Button size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <span className="tabular">
          Page {page} / {pages}
        </span>
        <Button size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </div>
  );
}

export function StatCard({ label, value, hint, tone, title }: { label: string; value: ReactNode; hint?: ReactNode; tone?: Tone; title?: string }) {
  return (
    <Card className="px-4 py-3.5" title={title}>
      <p className="text-[13px] text-muted">{label}</p>
      <p className={cn("tabular mt-1 text-2xl font-semibold tracking-tight text-ink", tone === "danger" && "text-danger", tone === "ok" && "text-ok")}>{value}</p>
      {hint && <p className="mt-0.5 text-xs text-faint">{hint}</p>}
    </Card>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2.5 text-sm sm:grid-cols-[160px_1fr]">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 break-words text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
