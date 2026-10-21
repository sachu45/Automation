import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  Ban,
  FileText,
  Inbox,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Mail,
  Megaphone,
  Menu,
  OctagonX,
  Radar,
  Send,
  Settings,
  Users,
  Workflow,
  X,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { ConfirmDialog } from "@/components/ui/dialog";
import { api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { SettingsResponse } from "@/types/api";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/leads", label: "Leads", icon: Users },
  { to: "/lists", label: "Lists", icon: ListChecks },
  { to: "/discovery", label: "Lead discovery", icon: Radar },
  { to: "/campaigns", label: "Campaigns", icon: Megaphone },
  { to: "/sequences", label: "Email sequences", icon: Workflow },
  { to: "/templates", label: "Templates", icon: FileText },
  { to: "/inbox", label: "Inbox", icon: Inbox },
  { to: "/accounts", label: "Sending accounts", icon: Send },
  { to: "/analytics", label: "Analytics", icon: BarChart3 },
  { to: "/suppressions", label: "Suppression list", icon: Ban },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function useAppSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: () => api.get<SettingsResponse>("/api/settings"), staleTime: 30_000 });
}

function ModeBanner() {
  const settings = useAppSettings();
  const qc = useQueryClient();
  const [confirmRelease, setConfirmRelease] = useState(false);
  const stop = useMutation({
    mutationFn: (engaged: boolean) => api.post("/api/settings/emergency-stop", { engaged, confirm: true }),
    onSuccess: (_d, engaged) => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      toast.success(engaged ? "Emergency stop engaged — nothing will send" : "Emergency stop released");
      setConfirmRelease(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const cfg = settings.data?.config;
  if (!cfg) return null;
  return (
    <>
      <div
        className={cn(
          "flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2 text-[13px] sm:px-6",
          cfg.emergency_stop ? "border-danger/30 bg-danger-soft text-danger" : cfg.dry_run ? "border-info/20 bg-info-soft text-info" : "border-ok/20 bg-ok-soft text-ok",
        )}
      >
        <span className="font-medium">
          {cfg.emergency_stop
            ? "Emergency stop engaged — all sending is halted."
            : cfg.dry_run
              ? "Dry-run mode — messages are rendered and simulated; nothing is sent."
              : "Live sending enabled — approved campaigns send real email."}
        </span>
        {cfg.emergency_stop ? (
          <Button size="sm" onClick={() => setConfirmRelease(true)}>
            Release stop
          </Button>
        ) : (
          <Button size="sm" variant="danger" onClick={() => stop.mutate(true)} loading={stop.isPending}>
            <OctagonX className="h-3.5 w-3.5" /> Emergency stop
          </Button>
        )}
      </div>
      <ConfirmDialog
        open={confirmRelease}
        onOpenChange={setConfirmRelease}
        title="Release emergency stop?"
        description="Active campaigns will resume sending according to their schedules and limits."
        confirmLabel="Release"
        onConfirm={() => stop.mutate(false)}
        loading={stop.isPending}
      />
    </>
  );
}

export function Layout() {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const location = useLocation();
  const inbox = useQuery({
    queryKey: ["inbox-count"],
    queryFn: () => api.get<{ total: number }>("/api/inbox", { needs_review: true, page_size: 1 }),
    refetchInterval: 60_000,
  });

  async function logout() {
    await api.post("/api/auth/logout");
    qc.clear();
    window.location.href = "/";
  }

  const nav = (
    <nav aria-label="Main" className="flex flex-1 flex-col gap-0.5 px-3">
      {NAV.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          end={n.end}
          onClick={() => setOpen(false)}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] font-medium transition-colors",
              isActive ? "bg-white/10 text-white" : "text-slate-300 hover:bg-white/5 hover:text-white",
            )
          }
        >
          <n.icon className="h-4 w-4 shrink-0" aria-hidden />
          <span className="flex-1">{n.label}</span>
          {n.to === "/inbox" && !!inbox.data?.total && (
            <span className="tabular rounded-full bg-brand px-1.5 text-[11px] font-semibold text-white" aria-label={`${inbox.data.total} to review`}>
              {inbox.data.total}
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );

  const brand = (
    <div className="flex items-center gap-2.5 px-5 py-5">
      <div className="rounded-lg bg-white/10 p-1.5 text-white">
        <Mail className="h-4 w-4" />
      </div>
      <span className="text-[15px] font-semibold text-white">Outreach</span>
    </div>
  );

  return (
    <div className="flex h-full">
      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 flex-col bg-[#0f172a] lg:flex">
        {brand}
        {nav}
        <div className="p-3">
          <button onClick={logout} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] text-slate-400 hover:bg-white/5 hover:text-white">
            <LogOut className="h-4 w-4" /> Sign out
          </button>
        </div>
      </aside>

      {/* Mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-ink/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-64 flex-col bg-[#0f172a]">
            <div className="flex items-center justify-between pr-3">
              {brand}
              <button aria-label="Close menu" className="rounded p-1.5 text-slate-300" onClick={() => setOpen(false)}>
                <X className="h-5 w-5" />
              </button>
            </div>
            {nav}
            <div className="p-3">
              <button onClick={logout} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] text-slate-400">
                <LogOut className="h-4 w-4" /> Sign out
              </button>
            </div>
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-border bg-surface px-4 py-3 lg:hidden">
          <button aria-label="Open menu" className="rounded-md p-1.5 text-ink-2 hover:bg-bg" onClick={() => setOpen(true)}>
            <Menu className="h-5 w-5" />
          </button>
          <span className="font-semibold">Outreach</span>
        </header>
        <ModeBanner />
        <main key={location.pathname.split("/")[1]} className="scroll-thin flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-[1280px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
