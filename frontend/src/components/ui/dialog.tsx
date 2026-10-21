import * as RD from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Button, Input } from "./index";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  const width = { sm: "max-w-md", md: "max-w-lg", lg: "max-w-2xl", xl: "max-w-4xl" }[size];
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-ink/40 backdrop-blur-[1px]" />
        <RD.Content
          className={cn(
            "fixed left-1/2 top-1/2 z-50 flex max-h-[92vh] w-[calc(100vw-24px)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-surface shadow-[var(--shadow-pop)] focus:outline-none",
            width,
          )}
        >
          <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
            <div>
              <RD.Title className="text-[15px] font-semibold text-ink">{title}</RD.Title>
              {description ? (
                <RD.Description className="mt-0.5 text-[13px] text-muted">{description}</RD.Description>
              ) : (
                <RD.Description className="sr-only">Dialog</RD.Description>
              )}
            </div>
            <RD.Close asChild>
              <Button variant="ghost" size="icon" aria-label="Close">
                <X className="h-4 w-4" />
              </Button>
            </RD.Close>
          </div>
          <div className="scroll-thin overflow-y-auto px-5 py-4">{children}</div>
          {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-border px-5 py-3">{footer}</div>}
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

/** Confirmation for consequential actions. With `typeToConfirm`, the user must retype a value. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  tone = "primary",
  typeToConfirm,
  loading,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  confirmLabel?: string;
  tone?: "primary" | "danger";
  typeToConfirm?: string;
  loading?: boolean;
  onConfirm: (typed: string) => void;
  children?: ReactNode;
}) {
  const [typed, setTyped] = useState("");
  const ok = !typeToConfirm || typed.trim() === typeToConfirm.trim();
  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) setTyped("");
        onOpenChange(v);
      }}
      title={title}
      description={description}
      size="sm"
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant={tone === "danger" ? "danger" : "primary"} disabled={!ok} loading={loading} onClick={() => onConfirm(typed)}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
      {typeToConfirm && (
        <div className="mt-3 space-y-1.5">
          <p className="text-[13px] text-ink-2">
            Type <span className="rounded bg-bg px-1 font-mono text-ink">{typeToConfirm}</span> to confirm.
          </p>
          <Input aria-label="Confirmation text" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus />
        </div>
      )}
    </Dialog>
  );
}
