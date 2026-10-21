import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App, { makeQueryClient } from "@/App";

const CONFIG = {
  enabled: false, test_mode: true, draft_generation_enabled: true, target_industries: [], company_sizes: [], countries: [], regions: [],
  job_titles: [], buyer_roles: [], keywords: ["python"], excluded_keywords: [], signal_categories: ["hiring"], excluded_domains: [],
  excluded_companies: [], max_results_per_run: 25, interval_minutes: 360, daily_limit: 100, min_relevance_score: 60, review_threshold: 40,
  skip_if_contacted_within_days: 90, weights: {}, enabled_sources: ["rss", "website", "manual"], feeds: [], watch_domains: [],
  max_pages_per_domain: 5, search_queries: [], enrichment_enabled: false, enrichment_confirmed: false, enrichment_daily_credit_budget: 0,
  ai_daily_limit: 50, ai_per_run_limit: 20, max_evidence_chars: 1500,
  offer: { product_description: "", value_proposition: "", call_to_action: "", target_audience: "", tone: "professional", length: "short" },
  evidence_retention_days: 180,
};
const STATUS = {
  enabled: false, test_mode: true, draft_generation_enabled: true, interval_minutes: 360, last_run: null, last_successful_run_at: null,
  next_run_at: null, signals_total: 3, by_processing_status: { drafted: 1 }, by_qualification: { qualified: 1 }, qualified: 1,
  drafts_created: 1, drafts_awaiting_review: 1, duplicates_skipped: 2, failed_jobs: 0, queued_jobs: 0, warnings: [],
};
const SIGNAL = {
  id: 7, source_provider: "rss", source_type: "feed_item", source_url: "https://acme.example/careers/python", category: "engineering_hiring",
  title: "We're hiring a Senior Python Engineer", company_name: "Acme", company_domain: "acme.example", contact_name: null, contact_title: null,
  lead_id: 3, published_at: "2026-10-12T09:00:00Z", discovered_at: "2026-10-19T09:00:00Z", excerpt: "Acme is hiring a senior Python engineer.",
  relevance_score: 72, qualification_status: "qualified", processing_status: "drafted", error_code: null, error_detail: null, test_mode: true,
  draft: { id: 11, status: "draft", subject: "Quick idea" }, reasons: [],
};
const PROVIDERS = [
  { name: "apollo", label: "Apollo.io", requires_key: true, cost: "People Search: 0 credits", configured: true, key_source: "stored", enabled: false,
    usage: { requests_today: 0, credits_today: 0, errors_today: 0, last_success_at: null, last_error_at: null, last_error: null } },
];
const SETTINGS = { config: { dry_run: true, emergency_stop: false, default_timezone: "UTC", default_window_start: "09:00", default_window_end: "17:00", default_send_days: [0], min_delay_seconds: 120 }, ai: {}, environment: {} };

type Routes = Record<string, unknown | ((init?: RequestInit) => Response)>;

function mockFetch(routes: Routes) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.split("?")[0];
    const r = routes[path];
    if (typeof r === "function") return (r as (i?: RequestInit) => Response)(init);
    const body = r ?? (["/api/inbox"].includes(path) ? { items: [], total: 0 } : []);
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const base: Routes = {
  "/api/auth/status": { authenticated: true, setup_required: false },
  "/api/settings": SETTINGS,
  "/api/discovery/status": STATUS,
  "/api/discovery/config": { config: CONFIG, categories: ["hiring", "engineering_hiring", "saas_launch"], sources: ["rss"] },
  "/api/discovery/runs": [],
  "/api/discovery/jobs/failed": [],
};

function go(path: string) {
  window.history.pushState({}, "", path);
  return render(<App client={makeQueryClient()} />);
}

afterEach(() => vi.unstubAllGlobals());

describe("lead discovery", () => {
  it("shows real counters and the no-send guarantee on the overview", async () => {
    mockFetch(base);
    go("/discovery");
    expect(await screen.findByRole("heading", { level: 1, name: "Lead discovery" })).toBeInTheDocument();
    expect(await screen.findByText(/never sends email, approves drafts/)).toBeInTheDocument();
    expect(screen.getByText("Duplicates skipped").nextSibling).toHaveTextContent("2");
  });

  it("signal inbox handles empty and error states", async () => {
    mockFetch({ ...base, "/api/discovery/signals": { items: [], total: 0, page: 1, page_size: 25 } });
    const { unmount } = go("/discovery?tab=signals");
    expect(await screen.findByText("No signals")).toBeInTheDocument();
    unmount();
    mockFetch({ ...base, "/api/discovery/signals": () => new Response(JSON.stringify({ error: { code: "x", message: "Database unavailable" } }), { status: 500 }) });
    go("/discovery?tab=signals");
    expect(await screen.findByText("Database unavailable", {}, { timeout: 5000 })).toBeInTheDocument(); // one automatic retry on 5xx first
  });

  it("draft review shows the evidence and links to the original source", async () => {
    mockFetch({
      ...base,
      "/api/discovery/drafts": [{ id: 11, status: "draft", subject: "Quick idea", body: "Hi,\n\n{{signature}}", label: "discovery v1", provider: "template",
        created_at: "2026-10-19T09:00:00Z", warnings: ["Number '37%' does not appear in your inputs"], evidence_refs: [], test_mode: true,
        lead: { id: 3, email: "cto@acme.example", name: "Riya", company: "Acme" }, signal: SIGNAL }],
    });
    go("/discovery?tab=drafts");
    expect(await screen.findByDisplayValue("Quick idea")).toBeInTheDocument();
    expect(screen.getByText("Acme is hiring a senior Python engineer.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open original source/ })).toHaveAttribute("href", "https://acme.example/careers/python");
    expect(screen.getByText(/does not appear in your inputs/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeInTheDocument();
  });

  it("never renders stored provider secrets", async () => {
    const fetchFn = mockFetch({ ...base, "/api/discovery/providers": PROVIDERS });
    go("/discovery?tab=sources");
    const input = await screen.findByLabelText("Apollo.io API key");
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("type", "password");
    expect(screen.getByText("key from stored")).toBeInTheDocument();
    expect(fetchFn.mock.calls.every(([u]) => !String(u).includes("key="))).toBe(true);
  });

  it("validates rules before saving", async () => {
    const fetchFn = mockFetch(base);
    const user = userEvent.setup();
    go("/discovery?tab=rules");
    const min = await screen.findByLabelText("Minimum score to draft (0–100)");
    await user.clear(min);
    await user.type(min, "20");
    await user.click(screen.getByRole("button", { name: "Save rules" }));
    expect(await screen.findByText("Review threshold must not exceed the minimum score")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Add feed/ }));
    await user.clear(min);
    await user.type(min, "60");
    await user.type(screen.getByLabelText("Feed URL"), "not a url");
    await user.click(screen.getByRole("button", { name: "Save rules" }));
    expect(await screen.findByText(/Invalid feed URL/)).toBeInTheDocument();
    await waitFor(() => expect(fetchFn.mock.calls.some(([u, i]) => u === "/api/discovery/config" && (i as RequestInit)?.method === "PATCH")).toBe(false));
  });

  it("manual signal dialog requires authorisation and pasted text for LinkedIn", async () => {
    mockFetch({ ...base, "/api/discovery/signals": { items: [], total: 0, page: 1, page_size: 25 } });
    const user = userEvent.setup();
    go("/discovery?tab=signals");
    await user.click(await screen.findByRole("button", { name: /Add signal/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Source URL"), "https://www.linkedin.com/posts/x");
    const submit = within(dialog).getByRole("button", { name: "Add signal" });
    expect(submit).toBeDisabled();
    expect(within(dialog).getByText("Post text (required for LinkedIn)")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Post text (required for LinkedIn)"), "We're hiring Python engineers");
    expect(submit).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(submit).toBeEnabled();
  });
});
