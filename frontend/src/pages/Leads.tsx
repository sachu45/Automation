import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, Plus, Search, Upload, Users } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Badge, Button, Card, Checkbox, EmptyState, ErrorState, Input, PageHeader, Pagination, Select, Spinner, StatusBadge, Table, Td, Th } from "@/components/ui";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { LeadForm, toPayload } from "@/features/leads/LeadForm";
import { api, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { Lead, LeadList, Page, Segment } from "@/types/api";

const STATUSES = ["new", "ready", "contacted", "replied", "interested", "not_interested", "bounced", "unsubscribed", "do_not_contact"];

export function LeadsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [creating, setCreating] = useState(false);
  const [dupesOpen, setDupesOpen] = useState(false);
  const [bulk, setBulk] = useState<{ action: string; value?: string } | null>(null);
  const [q, setQ] = useState(params.get("q") ?? "");

  const filters = {
    q: params.get("q") ?? "",
    status: params.get("status") ?? "",
    tag: params.get("tag") ?? "",
    list_id: params.get("list_id") ?? "",
    segment_id: params.get("segment_id") ?? "",
    source: params.get("source") ?? "",
    contacted: params.get("contacted") ?? "",
    sort: params.get("sort") ?? "created_at",
    order: params.get("order") ?? "desc",
    page: Number(params.get("page") ?? 1),
    page_size: 25,
  };
  const setFilter = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    if (k !== "page") next.delete("page");
    setParams(next);
    setSelected(new Set());
  };

  const leads = useQuery({ queryKey: ["leads", filters], queryFn: () => api.get<Page<Lead>>("/api/leads", filters), placeholderData: keepPreviousData });
  const lists = useQuery({ queryKey: ["lists"], queryFn: () => api.get<LeadList[]>("/api/lists") });
  const segments = useQuery({ queryKey: ["segments"], queryFn: () => api.get<Segment[]>("/api/segments") });
  const facets = useQuery({ queryKey: ["lead-facets"], queryFn: () => api.get<{ sources: string[]; tags: string[]; industries: string[] }>("/api/leads/facets") });

  const create = useMutation({
    mutationFn: (body: unknown) => api.post<Lead>("/api/leads", body),
    onSuccess: (lead) => {
      toast.success(`Lead ${lead.email} created`);
      setCreating(false);
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["lead-facets"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const bulkMut = useMutation({
    mutationFn: (b: { action: string; value?: string; confirm?: boolean }) => api.post<{ affected: number }>("/api/leads/bulk", { lead_ids: [...selected], ...b }),
    onSuccess: (r) => {
      toast.success(`Updated ${r.affected} lead(s)`);
      setBulk(null);
      setSelected(new Set());
      qc.invalidateQueries({ queryKey: ["leads"] });
      qc.invalidateQueries({ queryKey: ["lists"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const items = useMemo(() => leads.data?.items ?? [], [leads.data]);
  const allSelected = items.length > 0 && items.every((l) => selected.has(l.id));

  function toggleAll() {
    const next = new Set(selected);
    if (allSelected) items.forEach((l) => next.delete(l.id));
    else items.forEach((l) => next.add(l.id));
    setSelected(next);
  }

  async function exportCsv() {
    try {
      const { page: _p, page_size: _s, sort: _so, order: _o, ...f } = filters;
      await api.download("/api/leads/export", selected.size ? { lead_ids: [...selected] } : { filters: f }, "leads-export.csv");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  const sortHeader = (key: string, label: string) => (
    <button
      className="inline-flex items-center gap-1 hover:text-ink"
      onClick={() => {
        const next = new URLSearchParams(params);
        next.set("sort", key);
        next.set("order", filters.sort === key && filters.order === "desc" ? "asc" : "desc");
        setParams(next);
      }}
    >
      {label}
      {filters.sort === key && <span aria-hidden>{filters.order === "desc" ? "↓" : "↑"}</span>}
    </button>
  );

  return (
    <>
      <PageHeader
        title="Leads"
        description="Prospects and the companies they work for."
        actions={
          <>
            <Button onClick={() => setDupesOpen(true)}>
              <Copy className="h-4 w-4" /> Duplicates
            </Button>
            <Button onClick={exportCsv}>
              <Download className="h-4 w-4" /> Export{selected.size ? ` (${selected.size})` : ""}
            </Button>
            <Link to="/leads/import">
              <Button>
                <Upload className="h-4 w-4" /> Import CSV
              </Button>
            </Link>
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" /> New lead
            </Button>
          </>
        }
      />

      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <form
            className="relative min-w-[220px] flex-1"
            onSubmit={(e) => {
              e.preventDefault();
              setFilter("q", q);
            }}
          >
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
            <Input aria-label="Search leads" className="pl-9" placeholder="Search name, email, company, title…" value={q} onChange={(e) => setQ(e.target.value)} />
          </form>
          <Select aria-label="Status" className="w-auto" value={filters.status} onChange={(e) => setFilter("status", e.target.value)}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </Select>
          <Select aria-label="List" className="w-auto" value={filters.list_id} onChange={(e) => setFilter("list_id", e.target.value)}>
            <option value="">All lists</option>
            {lists.data?.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </Select>
          <Select aria-label="Segment" className="w-auto" value={filters.segment_id} onChange={(e) => setFilter("segment_id", e.target.value)}>
            <option value="">All segments</option>
            {segments.data?.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
          <Select aria-label="Tag" className="w-auto" value={filters.tag} onChange={(e) => setFilter("tag", e.target.value)}>
            <option value="">Any tag</option>
            {facets.data?.tags.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </Select>
          <Select aria-label="Source" className="w-auto" value={filters.source} onChange={(e) => setFilter("source", e.target.value)}>
            <option value="">Any source</option>
            {facets.data?.sources.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </Select>
          <Select aria-label="Contacted" className="w-auto" value={filters.contacted} onChange={(e) => setFilter("contacted", e.target.value)}>
            <option value="">Contacted or not</option>
            <option value="false">Never contacted</option>
            <option value="true">Contacted</option>
          </Select>
        </div>

        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-b border-border bg-brand-soft/60 px-4 py-2 text-sm">
            <span className="font-medium text-ink">{selected.size} selected</span>
            <Button size="sm" onClick={() => setBulk({ action: "add_tag" })}>
              Add tag
            </Button>
            <Button size="sm" onClick={() => setBulk({ action: "add_to_list" })}>
              Add to list
            </Button>
            <Button size="sm" onClick={() => setBulk({ action: "set_status" })}>
              Set status
            </Button>
            <Button size="sm" onClick={() => setBulk({ action: "do_not_contact" })}>
              Do not contact
            </Button>
            <Button size="sm" variant="danger" onClick={() => setBulk({ action: "delete" })}>
              Delete
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              Clear
            </Button>
          </div>
        )}

        {leads.isLoading ? (
          <Spinner />
        ) : leads.isError ? (
          <ErrorState error={leads.error} onRetry={() => leads.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<Users className="h-6 w-6" />}
            title={Object.values(filters).some((v, i) => i < 7 && v) ? "No leads match these filters" : "No leads yet"}
            description="Add a lead manually or import a CSV file you have the right to use."
            action={
              <Link to="/leads/import">
                <Button variant="primary">Import CSV</Button>
              </Link>
            }
          />
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th className="w-10">
                    <Checkbox aria-label="Select all on page" checked={allSelected} onChange={toggleAll} />
                  </Th>
                  <Th>{sortHeader("last_name", "Name")}</Th>
                  <Th>{sortHeader("email", "Email")}</Th>
                  <Th>Company</Th>
                  <Th>Title</Th>
                  <Th>{sortHeader("status", "Status")}</Th>
                  <Th>Tags</Th>
                  <Th>{sortHeader("last_contacted_at", "Last contacted")}</Th>
                  <Th>{sortHeader("created_at", "Added")}</Th>
                </tr>
              </thead>
              <tbody>
                {items.map((l) => (
                  <tr key={l.id} className="cursor-pointer hover:bg-bg/60" onClick={() => navigate(`/leads/${l.id}`)}>
                    <Td onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        aria-label={`Select ${l.email}`}
                        checked={selected.has(l.id)}
                        onChange={() => {
                          const next = new Set(selected);
                          if (next.has(l.id)) next.delete(l.id);
                          else next.add(l.id);
                          setSelected(next);
                        }}
                      />
                    </Td>
                    <Td className="font-medium text-ink">{l.full_name || [l.first_name, l.last_name].filter(Boolean).join(" ") || "—"}</Td>
                    <Td>
                      {l.email}
                      {l.suppressed && (
                        <Badge tone="danger" className="ml-2">
                          suppressed
                        </Badge>
                      )}
                    </Td>
                    <Td>{l.company ?? "—"}</Td>
                    <Td className="max-w-[200px] truncate">{l.job_title ?? "—"}</Td>
                    <Td>
                      <StatusBadge status={l.status} />
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {l.tags.slice(0, 3).map((t) => (
                          <Badge key={t}>{t}</Badge>
                        ))}
                        {l.tags.length > 3 && <span className="text-xs text-muted">+{l.tags.length - 3}</span>}
                      </div>
                    </Td>
                    <Td className="whitespace-nowrap">{fmtDate(l.last_contacted_at, false)}</Td>
                    <Td className="whitespace-nowrap">{fmtDate(l.created_at, false)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={filters.page} pageSize={filters.page_size} total={leads.data?.total ?? 0} onPage={(p) => setFilter("page", String(p))} />
          </>
        )}
      </Card>

      <Dialog open={creating} onOpenChange={setCreating} title="New lead" size="lg">
        <LeadForm onSubmit={(v) => create.mutate(toPayload(v))} submitting={create.isPending} onCancel={() => setCreating(false)} />
      </Dialog>

      <BulkDialog bulk={bulk} lists={lists.data ?? []} onClose={() => setBulk(null)} loading={bulkMut.isPending} onRun={(b) => bulkMut.mutate(b)} count={selected.size} />
      <DuplicatesDialog open={dupesOpen} onOpenChange={setDupesOpen} />
    </>
  );
}

function BulkDialog({
  bulk,
  lists,
  onClose,
  onRun,
  loading,
  count,
}: {
  bulk: { action: string } | null;
  lists: LeadList[];
  onClose: () => void;
  onRun: (b: { action: string; value?: string; confirm?: boolean }) => void;
  loading: boolean;
  count: number;
}) {
  const [value, setValue] = useState("");
  if (!bulk) return null;
  const destructive = bulk.action === "delete" || bulk.action === "do_not_contact";
  if (destructive) {
    return (
      <ConfirmDialog
        open
        onOpenChange={(v) => !v && onClose()}
        title={bulk.action === "delete" ? `Delete ${count} lead(s)?` : `Mark ${count} lead(s) as do-not-contact?`}
        description={
          bulk.action === "delete"
            ? "Lead records are removed. Suppression entries are kept so deleted people are never re-contacted."
            : "They are added to the suppression list and any scheduled messages are cancelled."
        }
        tone="danger"
        confirmLabel={bulk.action === "delete" ? "Delete" : "Suppress"}
        typeToConfirm={bulk.action === "delete" ? "DELETE" : undefined}
        loading={loading}
        onConfirm={() => onRun({ action: bulk.action, confirm: true })}
      />
    );
  }
  const title = { add_tag: "Add tag", add_to_list: "Add to list", set_status: "Set status" }[bulk.action] ?? bulk.action;
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={`${title} — ${count} lead(s)`}
      size="sm"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!value} loading={loading} onClick={() => onRun({ action: bulk.action, value })}>
            Apply
          </Button>
        </>
      }
    >
      {bulk.action === "add_tag" && <Input aria-label="Tag" placeholder="tag name" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />}
      {bulk.action === "add_to_list" && (
        <Select aria-label="List" value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">Choose a list…</option>
          {lists.map((l) => (
            <option key={l.id} value={l.id}>
              {l.name}
            </option>
          ))}
        </Select>
      )}
      {bulk.action === "set_status" && (
        <Select aria-label="Status" value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">Choose a status…</option>
          {["new", "ready", "contacted", "replied", "interested", "not_interested"].map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </Select>
      )}
    </Dialog>
  );
}

function DuplicatesDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient();
  const dupes = useQuery({ queryKey: ["duplicates"], queryFn: () => api.get<{ reason: string; leads: Lead[] }[]>("/api/leads/duplicates"), enabled: open });
  const [pending, setPending] = useState<{ primary: Lead; others: Lead[] } | null>(null);
  const merge = useMutation({
    mutationFn: (p: { primary: Lead; others: Lead[] }) => api.post("/api/leads/merge", { primary_id: p.primary.id, duplicate_ids: p.others.map((o) => o.id), confirm: true }),
    onSuccess: () => {
      toast.success("Leads merged");
      setPending(null);
      qc.invalidateQueries({ queryKey: ["duplicates"] });
      qc.invalidateQueries({ queryKey: ["leads"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange} title="Possible duplicates" description="Same address (ignoring +tags and Gmail dots) or same name at the same company. Nothing merges without your confirmation." size="lg">
        {dupes.isLoading ? (
          <Spinner />
        ) : !dupes.data?.length ? (
          <EmptyState title="No likely duplicates found" />
        ) : (
          <div className="space-y-3">
            {dupes.data.map((g, i) => (
              <Card key={i} className="p-3">
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">{g.reason === "same_address" ? "Same address" : "Same name & company"}</p>
                <ul className="space-y-1.5">
                  {g.leads.map((l) => (
                    <li key={l.id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
                      <span>
                        <span className="font-medium">{l.full_name ?? l.email}</span> <span className="text-muted">{l.email}</span>
                      </span>
                      <Button size="sm" onClick={() => setPending({ primary: l, others: g.leads.filter((x) => x.id !== l.id) })}>
                        Keep this one
                      </Button>
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        )}
      </Dialog>
      {pending && (
        <ConfirmDialog
          open
          onOpenChange={(v) => !v && setPending(null)}
          title="Merge leads?"
          description={`${pending.primary.email} is kept. Empty fields are filled from the others; existing values are not overwritten. ${pending.others.map((o) => o.email).join(", ")} will be removed.`}
          confirmLabel="Merge"
          loading={merge.isPending}
          onConfirm={() => merge.mutate(pending)}
        />
      )}
    </>
  );
}
