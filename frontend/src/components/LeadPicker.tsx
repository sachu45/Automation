import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Input } from "@/components/ui";
import { api } from "@/lib/api";
import type { Lead, Page } from "@/types/api";

/** Search-as-you-type lead selector. */
export function LeadPicker({ value, onChange, label = "Preview with lead" }: { value: Lead | null; onChange: (l: Lead | null) => void; label?: string }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const results = useQuery({
    queryKey: ["lead-search", q],
    queryFn: () => api.get<Page<Lead>>("/api/leads", { q, page_size: 8 }),
    enabled: open,
  });
  return (
    <div className="relative">
      <label className="mb-1.5 block text-[13px] font-medium text-ink-2" htmlFor="lead-picker">
        {label}
      </label>
      {value ? (
        <div className="flex h-9 items-center justify-between rounded-lg border border-border-strong bg-surface px-3 text-sm">
          <span className="truncate">
            {value.full_name ?? value.email} <span className="text-muted">{value.email}</span>
          </span>
          <button className="text-xs text-brand hover:underline" onClick={() => onChange(null)}>
            change
          </button>
        </div>
      ) : (
        <Input
          id="lead-picker"
          placeholder="Search leads… (blank = sample data)"
          value={q}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onChange={(e) => setQ(e.target.value)}
          autoComplete="off"
        />
      )}
      {open && !value && !!results.data?.items.length && (
        <ul className="absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-border bg-surface py-1 shadow-[var(--shadow-pop)]" role="listbox">
          {results.data.items.map((l) => (
            <li key={l.id}>
              <button
                role="option"
                aria-selected={false}
                className="w-full px-3 py-2 text-left text-sm hover:bg-bg"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onChange(l);
                  setOpen(false);
                }}
              >
                <span className="font-medium">{l.full_name ?? l.email}</span>
                <span className="ml-2 text-muted">{l.email}</span>
                {l.company && <span className="ml-2 text-xs text-faint">{l.company}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
