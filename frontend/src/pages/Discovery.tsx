import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ErrorState, PageHeader, Spinner, Tabs } from "@/components/ui";
import { DiscoveryDrafts } from "@/features/discovery/Drafts";
import { DiscoveryOverview, useDiscoveryStatus } from "@/features/discovery/Overview";
import { DiscoveryRules } from "@/features/discovery/Rules";
import { DiscoverySignals } from "@/features/discovery/Signals";
import { DiscoverySources } from "@/features/discovery/Sources";
import { api } from "@/lib/api";
import type { DiscoveryConfig } from "@/types/api";

type Tab = "overview" | "signals" | "drafts" | "rules" | "sources";

export function DiscoveryPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "overview";
  const status = useDiscoveryStatus();
  const cfg = useQuery({ queryKey: ["discovery", "config"], queryFn: () => api.get<{ config: DiscoveryConfig; categories: string[] }>("/api/discovery/config") });
  const awaiting = status.data?.drafts_awaiting_review ?? 0;
  return (
    <>
      <PageHeader
        title="Lead discovery"
        description="Finds business signals from sources you choose, records the evidence, scores relevance and writes drafts for your review. It never sends email."
      />
      <Tabs
        value={tab}
        onChange={(t) => setParams({ tab: t })}
        tabs={[
          { value: "overview", label: "Overview" },
          { value: "signals", label: "Signal inbox" },
          { value: "drafts", label: `Draft review${awaiting ? ` (${awaiting})` : ""}` },
          { value: "rules", label: "Rules" },
          { value: "sources", label: "Sources & integrations" },
        ]}
      />
      {tab === "overview" && <DiscoveryOverview />}
      {tab === "drafts" && <DiscoveryDrafts />}
      {(tab === "signals" || tab === "rules" || tab === "sources") &&
        (cfg.isLoading ? (
          <Spinner />
        ) : cfg.isError || !cfg.data ? (
          <ErrorState error={cfg.error} onRetry={() => cfg.refetch()} />
        ) : tab === "signals" ? (
          <DiscoverySignals categories={cfg.data.categories} />
        ) : tab === "rules" ? (
          <DiscoveryRules config={cfg.data.config} categories={cfg.data.categories} />
        ) : (
          <DiscoverySources config={cfg.data.config} />
        ))}
    </>
  );
}
