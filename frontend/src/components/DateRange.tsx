import { Input, Select } from "@/components/ui";
import { isoDate } from "@/lib/utils";

export interface Range {
  start: string;
  end: string;
}

export function defaultRange(days = 30): Range {
  const end = new Date();
  const start = new Date(Date.now() - days * 86400_000);
  return { start: isoDate(start), end: isoDate(end) };
}

export function DateRangePicker({ value, onChange }: { value: Range; onChange: (r: Range) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select
        aria-label="Preset range"
        className="w-auto"
        value=""
        onChange={(e) => e.target.value && onChange(defaultRange(Number(e.target.value)))}
      >
        <option value="">Quick range…</option>
        <option value="7">Last 7 days</option>
        <option value="30">Last 30 days</option>
        <option value="90">Last 90 days</option>
        <option value="365">Last 12 months</option>
      </Select>
      <Input aria-label="Start date" type="date" className="w-auto" value={value.start} max={value.end} onChange={(e) => onChange({ ...value, start: e.target.value })} />
      <span className="text-muted">–</span>
      <Input aria-label="End date" type="date" className="w-auto" value={value.end} min={value.start} onChange={(e) => onChange({ ...value, end: e.target.value })} />
    </div>
  );
}
