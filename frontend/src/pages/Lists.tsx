import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Filter, ListChecks, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Button, Card, EmptyState, Field, Input, PageHeader, Select, Spinner, Table, Tabs, Td, Textarea, Th } from "@/components/ui";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { Lead, LeadList, Segment, SegmentRule } from "@/types/api";

export function ListsPage() {
  const [tab, setTab] = useState<"lists" | "segments">("lists");
  return (
    <>
      <PageHeader title="Lists & segments" description="Static lists hold hand-picked leads. Segments are saved filters evaluated live against the database." />
      <Tabs value={tab} onChange={setTab} tabs={[{ value: "lists", label: "Static lists" }, { value: "segments", label: "Segments" }]} />
      {tab === "lists" ? <StaticLists /> : <Segments />}
    </>
  );
}

function StaticLists() {
  const qc = useQueryClient();
  const lists = useQuery({ queryKey: ["lists"], queryFn: () => api.get<LeadList[]>("/api/lists") });
  const [editing, setEditing] = useState<Partial<LeadList> | null>(null);
  const [deleting, setDeleting] = useState<LeadList | null>(null);
  const save = useMutation({
    mutationFn: (l: Partial<LeadList>) => (l.id ? api.patch(`/api/lists/${l.id}`, { name: l.name, description: l.description }) : api.post("/api/lists", { name: l.name, description: l.description })),
    onSuccess: () => {
      toast.success("List saved");
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["lists"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/api/lists/${id}`),
    onSuccess: () => {
      toast.success("List deleted (leads kept)");
      setDeleting(null);
      qc.invalidateQueries({ queryKey: ["lists"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Card>
      <div className="flex justify-end border-b border-border p-3">
        <Button variant="primary" onClick={() => setEditing({ name: "", description: "" })}>
          <Plus className="h-4 w-4" /> New list
        </Button>
      </div>
      {lists.isLoading ? (
        <Spinner />
      ) : !lists.data?.length ? (
        <EmptyState icon={<ListChecks className="h-6 w-6" />} title="No lists yet" description="Create a list, then add leads from the Leads table (select → Add to list) or during CSV import." />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Description</Th>
              <Th className="text-right">Leads</Th>
              <Th>Created</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {lists.data.map((l) => (
              <tr key={l.id}>
                <Td className="font-medium text-ink">
                  <Link to={`/leads?list_id=${l.id}`} className="hover:text-brand">
                    {l.name}
                  </Link>
                </Td>
                <Td className="max-w-[360px] truncate">{l.description ?? "—"}</Td>
                <Td className="tabular text-right">{l.lead_count.toLocaleString()}</Td>
                <Td>{fmtDate(l.created_at, false)}</Td>
                <Td className="text-right">
                  <Button size="sm" variant="ghost" onClick={() => setEditing(l)}>
                    Edit
                  </Button>
                  <Button size="icon" variant="ghost" aria-label={`Delete ${l.name}`} onClick={() => setDeleting(l)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <Dialog
        open={!!editing}
        onOpenChange={(v) => !v && setEditing(null)}
        title={editing?.id ? "Edit list" : "New list"}
        size="sm"
        footer={
          <>
            <Button onClick={() => setEditing(null)}>Cancel</Button>
            <Button variant="primary" disabled={!editing?.name?.trim()} loading={save.isPending} onClick={() => editing && save.mutate(editing)}>
              Save
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Name" htmlFor="ln">
            <Input id="ln" value={editing?.name ?? ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
          </Field>
          <Field label="Description" htmlFor="ld">
            <Textarea id="ld" value={editing?.description ?? ""} onChange={(e) => setEditing({ ...editing, description: e.target.value })} />
          </Field>
        </div>
      </Dialog>
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(v) => !v && setDeleting(null)}
        title={`Delete list "${deleting?.name}"?`}
        description="Only the list is removed — the leads stay in your database."
        tone="danger"
        confirmLabel="Delete"
        loading={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.id)}
      />
    </Card>
  );
}

const FIELDS: { value: string; label: string; type: "text" | "date" | "bool" | "tag" | "list" }[] = [
  { value: "job_title", label: "Job title", type: "text" },
  { value: "company", label: "Company", type: "text" },
  { value: "industry", label: "Industry", type: "text" },
  { value: "country", label: "Country", type: "text" },
  { value: "source", label: "Lead source", type: "text" },
  { value: "status", label: "Status", type: "text" },
  { value: "email_domain", label: "Email domain", type: "text" },
  { value: "tag", label: "Tag", type: "tag" },
  { value: "list", label: "List membership", type: "list" },
  { value: "never_contacted", label: "Never contacted", type: "bool" },
  { value: "has_replied", label: "Has replied", type: "bool" },
  { value: "created_at", label: "Created", type: "date" },
  { value: "last_contacted_at", label: "Last contacted", type: "date" },
];
const OPS: Record<string, { value: string; label: string }[]> = {
  text: [
    { value: "contains", label: "contains" },
    { value: "eq", label: "is" },
    { value: "neq", label: "is not" },
    { value: "starts_with", label: "starts with" },
    { value: "not_contains", label: "does not contain" },
    { value: "in", label: "is any of (comma separated)" },
    { value: "is_empty", label: "is empty" },
    { value: "is_not_empty", label: "is not empty" },
  ],
  date: [
    { value: "within_days", label: "within last N days" },
    { value: "older_than_days", label: "more than N days ago" },
    { value: "before", label: "before date" },
    { value: "after", label: "on/after date" },
    { value: "is_empty", label: "never" },
  ],
  bool: [
    { value: "is_true", label: "yes" },
    { value: "is_false", label: "no" },
  ],
  tag: [
    { value: "has", label: "has tag" },
    { value: "not_has", label: "does not have tag" },
  ],
  list: [
    { value: "in", label: "is in list" },
    { value: "not_in", label: "is not in list" },
  ],
};

function Segments() {
  const qc = useQueryClient();
  const segments = useQuery({ queryKey: ["segments"], queryFn: () => api.get<Segment[]>("/api/segments") });
  const [editing, setEditing] = useState<Partial<Segment> | null>(null);
  const [deleting, setDeleting] = useState<Segment | null>(null);
  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/api/segments/${id}`),
    onSuccess: () => {
      toast.success("Segment deleted");
      setDeleting(null);
      qc.invalidateQueries({ queryKey: ["segments"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card>
      <div className="flex justify-end border-b border-border p-3">
        <Button variant="primary" onClick={() => setEditing({ name: "", match: "all", rules: [{ field: "job_title", op: "contains", value: "" }] })}>
          <Plus className="h-4 w-4" /> New segment
        </Button>
      </div>
      {segments.isLoading ? (
        <Spinner />
      ) : !segments.data?.length ? (
        <EmptyState
          icon={<Filter className="h-6 w-6" />}
          title="No segments yet"
          description="Examples: founders at SaaS companies, leads never contacted, leads imported from an event, leads who replied."
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>Rules</Th>
              <Th className="text-right">Matching leads (live)</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {segments.data.map((s) => (
              <tr key={s.id}>
                <Td className="font-medium text-ink">
                  <Link to={`/leads?segment_id=${s.id}`} className="hover:text-brand">
                    {s.name}
                  </Link>
                </Td>
                <Td className="max-w-[420px] text-xs">
                  {s.rules.map((r) => `${r.field} ${r.op}${r.value !== null && r.value !== "" && r.value !== undefined ? ` "${r.value}"` : ""}`).join(s.match === "all" ? " AND " : " OR ") || "all leads"}
                </Td>
                <Td className="tabular text-right">{s.lead_count.toLocaleString()}</Td>
                <Td className="text-right">
                  <Button size="sm" variant="ghost" onClick={() => setEditing(s)}>
                    Edit
                  </Button>
                  <Button size="icon" variant="ghost" aria-label={`Delete ${s.name}`} onClick={() => setDeleting(s)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {editing && <SegmentEditor segment={editing} onClose={() => setEditing(null)} />}
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(v) => !v && setDeleting(null)}
        title={`Delete segment "${deleting?.name}"?`}
        description="Leads are not affected."
        tone="danger"
        confirmLabel="Delete"
        loading={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.id)}
      />
    </Card>
  );
}

function SegmentEditor({ segment, onClose }: { segment: Partial<Segment>; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(segment.name ?? "");
  const [match, setMatch] = useState<"all" | "any">(segment.match ?? "all");
  const [rules, setRules] = useState<SegmentRule[]>(segment.rules ?? []);
  const lists = useQuery({ queryKey: ["lists"], queryFn: () => api.get<LeadList[]>("/api/lists") });
  const preview = useQuery({
    queryKey: ["segment-preview", match, rules],
    queryFn: () => api.post<{ total: number; sample: Lead[] }>("/api/segments/preview", { name: name || "preview", match, rules }),
    retry: false,
  });
  const save = useMutation({
    mutationFn: () => {
      const body = { name, match, rules, description: segment.description ?? null };
      return segment.id ? api.put(`/api/segments/${segment.id}`, body) : api.post("/api/segments", body);
    },
    onSuccess: () => {
      toast.success("Segment saved");
      qc.invalidateQueries({ queryKey: ["segments"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const update = (i: number, patch: Partial<SegmentRule>) => setRules(rules.map((r, j) => (i === j ? { ...r, ...patch } : r)));

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={segment.id ? "Edit segment" : "New segment"}
      size="xl"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!name.trim() || preview.isError} loading={save.isPending} onClick={() => save.mutate()}>
            Save segment
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_200px]">
          <Field label="Name" htmlFor="sn">
            <Input id="sn" value={name} onChange={(e) => setName(e.target.value)} placeholder="Founders at SaaS companies" />
          </Field>
          <Field label="Match" htmlFor="sm">
            <Select id="sm" value={match} onChange={(e) => setMatch(e.target.value as "all" | "any")}>
              <option value="all">All rules (AND)</option>
              <option value="any">Any rule (OR)</option>
            </Select>
          </Field>
        </div>
        <div className="space-y-2">
          {rules.map((r, i) => {
            const meta = FIELDS.find((f) => f.value === r.field) ?? FIELDS[0];
            const ops = OPS[meta.type];
            const needsValue = !["is_empty", "is_not_empty", "is_true", "is_false"].includes(r.op);
            return (
              <div key={i} className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-bg/40 p-2">
                <Select aria-label="Field" className="w-44" value={r.field} onChange={(e) => {
                  const f = FIELDS.find((x) => x.value === e.target.value)!;
                  update(i, { field: f.value, op: OPS[f.type][0].value, value: "" });
                }}>
                  {FIELDS.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.label}
                    </option>
                  ))}
                </Select>
                <Select aria-label="Operator" className="w-56" value={r.op} onChange={(e) => update(i, { op: e.target.value })}>
                  {ops.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
                {needsValue &&
                  (meta.type === "list" ? (
                    <Select aria-label="List" className="w-56" value={String(r.value ?? "")} onChange={(e) => update(i, { value: e.target.value })}>
                      <option value="">Choose list…</option>
                      {lists.data?.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.name}
                        </option>
                      ))}
                    </Select>
                  ) : (
                    <Input
                      aria-label="Value"
                      className="w-56 flex-1"
                      type={meta.type === "date" && ["before", "after"].includes(r.op) ? "date" : meta.type === "date" ? "number" : "text"}
                      value={String(r.value ?? "")}
                      onChange={(e) => update(i, { value: e.target.value })}
                    />
                  ))}
                <Button size="icon" variant="ghost" aria-label="Remove rule" onClick={() => setRules(rules.filter((_, j) => j !== i))}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            );
          })}
          <Button size="sm" onClick={() => setRules([...rules, { field: "industry", op: "eq", value: "" }])}>
            <Plus className="h-3.5 w-3.5" /> Add rule
          </Button>
        </div>
        <div className="rounded-lg border border-border p-3">
          {preview.isError ? (
            <p className="text-sm text-danger">{errorMessage(preview.error)}</p>
          ) : (
            <>
              <p className="text-sm">
                <span className="tabular font-semibold">{preview.data?.total ?? "…"}</span> matching lead(s) right now
              </p>
              {!!preview.data?.sample.length && (
                <ul className="mt-2 grid grid-cols-1 gap-1 text-xs text-muted sm:grid-cols-2">
                  {preview.data.sample.slice(0, 10).map((l) => (
                    <li key={l.id} className="truncate">
                      {l.full_name ?? l.email} · {l.job_title ?? "—"} · {l.company ?? "—"}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </div>
    </Dialog>
  );
}
