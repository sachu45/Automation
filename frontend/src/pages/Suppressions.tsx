import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Plus, Search, Upload } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Alert, Button, Card, EmptyState, Field, Input, PageHeader, Pagination, Select, Spinner, StatusBadge, Table, Td, Textarea, Th } from "@/components/ui";
import { Dialog } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { fmtDate, titleCase } from "@/lib/utils";
import type { Page, Suppression } from "@/types/api";

const REASONS = ["unsubscribed", "hard_bounce", "do_not_contact", "invalid_address", "manual", "complaint", "other"];

export function SuppressionsPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [reason, setReason] = useState("");
  const [active, setActive] = useState("true");
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [lifting, setLifting] = useState<Suppression | null>(null);
  const list = useQuery({
    queryKey: ["suppressions", q, reason, active, page],
    queryFn: () => api.get<Page<Suppression>>("/api/suppressions", { q, reason, active, page, page_size: 50 }),
    placeholderData: keepPreviousData,
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["suppressions"] });

  return (
    <>
      <PageHeader
        title="Suppression list"
        description="Addresses and domains that must never be emailed. Checked at import, at approval, and immediately before every send. Entries survive lead deletion."
        actions={
          <>
            <Button onClick={() => setBulkOpen(true)}>
              <Upload className="h-4 w-4" /> Bulk add
            </Button>
            <Button variant="primary" onClick={() => setAdding(true)}>
              <Plus className="h-4 w-4" /> Add
            </Button>
          </>
        }
      />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-border p-3">
          <div className="relative min-w-[220px] flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
            <Input aria-label="Search suppressions" className="pl-9" placeholder="Search email or domain" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
          </div>
          <Select aria-label="Reason" className="w-auto" value={reason} onChange={(e) => { setReason(e.target.value); setPage(1); }}>
            <option value="">All reasons</option>
            {REASONS.map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}
          </Select>
          <Select aria-label="Active" className="w-auto" value={active} onChange={(e) => { setActive(e.target.value); setPage(1); }}>
            <option value="true">Active</option>
            <option value="false">Lifted</option>
            <option value="">All</option>
          </Select>
        </div>
        {list.isLoading ? (
          <Spinner />
        ) : !list.data?.items.length ? (
          <EmptyState icon={<Ban className="h-6 w-6" />} title="Nothing suppressed" description="Unsubscribes, hard bounces and do-not-contact requests appear here automatically." />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Email / domain</Th>
                  <Th>Reason</Th>
                  <Th>Source</Th>
                  <Th>Notes</Th>
                  <Th>Added</Th>
                  <Th>State</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((s) => (
                  <tr key={s.id}>
                    <Td className="font-medium text-ink">{s.email ?? `@${s.domain}`}</Td>
                    <Td><StatusBadge status={s.reason === "unsubscribed" ? "unsubscribed" : s.reason === "hard_bounce" ? "bounced" : "do_not_contact"} label={titleCase(s.reason)} /></Td>
                    <Td className="text-xs">{s.source.replace(/_/g, " ")}</Td>
                    <Td className="max-w-[240px] truncate text-xs">{s.notes ?? ""}</Td>
                    <Td className="whitespace-nowrap">{fmtDate(s.created_at)}</Td>
                    <Td>{s.active ? <StatusBadge status="suppressed" label="Active" /> : <span className="text-xs text-muted" title={s.lifted_reason ?? ""}>Lifted {fmtDate(s.lifted_at, false)}</span>}</Td>
                    <Td className="text-right">{s.active && <Button size="sm" variant="ghost" onClick={() => setLifting(s)}>Correct…</Button>}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={page} pageSize={50} total={list.data.total} onPage={setPage} />
          </>
        )}
      </Card>
      {adding && <AddDialog onClose={() => { setAdding(false); invalidate(); }} />}
      {bulkOpen && <BulkDialog onClose={() => { setBulkOpen(false); invalidate(); }} />}
      {lifting && <LiftDialog s={lifting} onClose={() => { setLifting(null); invalidate(); }} />}
    </>
  );
}

function AddDialog({ onClose }: { onClose: () => void }) {
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("manual");
  const [notes, setNotes] = useState("");
  const save = useMutation({
    mutationFn: () => api.post("/api/suppressions", { ...(value.includes("@") ? { email: value.trim() } : { domain: value.trim() }), reason, notes: notes || null }),
    onSuccess: () => { toast.success("Suppressed; scheduled messages cancelled"); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Add suppression" size="sm"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!value.trim()} loading={save.isPending} onClick={() => save.mutate()}>Suppress</Button></>}>
      <div className="space-y-3">
        <Field label="Email address or domain" htmlFor="sv" hint="A domain (example.com) blocks every address at it"><Input id="sv" value={value} onChange={(e) => setValue(e.target.value)} autoFocus /></Field>
        <Field label="Reason" htmlFor="sr"><Select id="sr" value={reason} onChange={(e) => setReason(e.target.value)}>{REASONS.map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}</Select></Field>
        <Field label="Notes" htmlFor="sn"><Input id="sn" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
      </div>
    </Dialog>
  );
}

function BulkDialog({ onClose }: { onClose: () => void }) {
  const [text, setText] = useState("");
  const [reason, setReason] = useState("do_not_contact");
  const save = useMutation({
    mutationFn: () => api.post<{ added: number }>("/api/suppressions/bulk", { text, reason }),
    onSuccess: (r) => { toast.success(`${r.added} entries suppressed`); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Bulk add suppressions" description="Paste one email or domain per line (or a CSV whose first column is the address)." size="md"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!text.trim()} loading={save.isPending} onClick={() => save.mutate()}>Add all</Button></>}>
      <div className="space-y-3">
        <Textarea aria-label="Addresses" rows={10} className="font-mono text-[13px]" value={text} onChange={(e) => setText(e.target.value)} />
        <Field label="Reason" htmlFor="br"><Select id="br" value={reason} onChange={(e) => setReason(e.target.value)}>{REASONS.map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}</Select></Field>
      </div>
    </Dialog>
  );
}

function LiftDialog({ s, onClose }: { s: Suppression; onClose: () => void }) {
  const [confirmValue, setConfirmValue] = useState("");
  const [reason, setReason] = useState("");
  const target = s.email ?? s.domain ?? "";
  const locked = s.reason === "complaint" || (s.reason === "unsubscribed" && ["unsubscribe_link", "one_click", "reply"].includes(s.source));
  const lift = useMutation({
    mutationFn: () => api.post(`/api/suppressions/${s.id}/lift`, { confirm_value: confirmValue, reason }),
    onSuccess: () => { toast.success("Suppression lifted (recorded in the audit log)"); onClose(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Correct a suppression" size="sm"
      footer={<><Button onClick={onClose}>Cancel</Button>{!locked && <Button variant="danger" disabled={confirmValue.trim().toLowerCase() !== target || reason.trim().length < 5} loading={lift.isPending} onClick={() => lift.mutate()}>Lift suppression</Button>}</>}>
      {locked ? (
        <Alert tone="danger" title="This entry can't be lifted here">
          The recipient opted out (or complained) themselves. Only they can opt back in — see docs/OPERATIONS.md for the documented manual procedure.
        </Alert>
      ) : (
        <div className="space-y-3">
          <Alert tone="warn">Only lift a suppression to fix a mistake. The original record is kept and the change is audited.</Alert>
          <Field label={`Type ${target} to confirm`} htmlFor="lc"><Input id="lc" value={confirmValue} onChange={(e) => setConfirmValue(e.target.value)} /></Field>
          <Field label="Reason for the correction" htmlFor="lr"><Textarea id="lr" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        </div>
      )}
    </Dialog>
  );
}
