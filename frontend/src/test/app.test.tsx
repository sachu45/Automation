import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { makeQueryClient } from "@/App";

const SETTINGS = {
  config: {
    sender_name: "Sam", default_sender_email: null, reply_to_email: null, company_name: "", company_website: "", mailing_address: "", signature: "",
    default_timezone: "UTC", default_window_start: "09:00", default_window_end: "17:00", default_send_days: [0, 1, 2, 3, 4],
    daily_sending_limit: 50, min_delay_seconds: 120, followup_default_delay_days: 3, max_followups: 3, dry_run: true, emergency_stop: false,
    append_opt_out_footer: true, opt_out_text: "reply unsubscribe", auto_suppress_unsubscribe_replies: true, stop_on_out_of_office: false,
    ai_provider: "env", ai_base_url: "", ai_model: "", ai_classify_replies: false,
    retention_inbound_body_days: 180, retention_outbound_body_days: 180, retention_audit_days: 0,
  },
  ai: { provider: "template", model: null, base_url: null, has_api_key: false, ready: true, ai_enabled: false },
  environment: { app_env: "test", public_base_url: null, unsubscribe_links: false, database: "sqlite", bind_host: "127.0.0.1", cookie_secure: false },
  live_sending_phrase: "ENABLE LIVE SENDING",
  safety_fields: [],
};

const page = { items: [], total: 0, page: 1, page_size: 25, counts: {} };

function respond(url: string, auth: { authenticated: boolean; setup_required: boolean }) {
  const path = url.split("?")[0];
  if (path === "/api/auth/status") return auth;
  if (path === "/api/settings") return SETTINGS;
  if (path === "/api/health") return { status: "ok", worker_suspected_down: false, oldest_due_job_seconds: 0 };
  if (path === "/api/analytics/summary") return { range: {}, metrics: { total_leads: 42, accepted: 7 }, definitions: { total_leads: "All leads" } };
  if (path === "/api/analytics/timeseries") return [];
  if (path === "/api/leads/facets") return { sources: [], tags: [], industries: [] };
  if (path === "/api/templates/variables") return { lead: ["first_name"], sender: ["sender_name"], system: [] };
  if (path === "/api/ai/status") return SETTINGS.ai;
  if (path === "/api/ai/usage") return { by_purpose: [], recent_errors: [] };
  if (path === "/api/discovery/status") return { enabled: false, test_mode: true, interval_minutes: 360, by_processing_status: {}, by_qualification: {}, warnings: [], signals_total: 0, qualified: 0, drafts_created: 0, drafts_awaiting_review: 0, duplicates_skipped: 0, failed_jobs: 0, queued_jobs: 0, last_run: null };
  if (path === "/api/discovery/config") return { config: {}, categories: [], sources: [] };
  if (["/api/leads", "/api/inbox", "/api/suppressions", "/api/settings/audit"].includes(path)) return page;
  return [];
}

let fetchMock: ReturnType<typeof vi.fn>;

function setup(auth = { authenticated: true, setup_required: false }) {
  fetchMock = vi.fn(async (url: string) => new Response(JSON.stringify(respond(url, auth)), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => {
  window.history.pushState({}, "", "/");
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("app shell", () => {
  it("loads the dashboard with real numbers from the API", async () => {
    setup();
    render(<App client={makeQueryClient()} />);
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(await screen.findByText("42")).toBeInTheDocument();
    expect(screen.getByText(/Dry-run mode/)).toBeInTheDocument();
  });

  it("navigates to every sidebar section", async () => {
    setup();
    const user = userEvent.setup();
    render(<App client={makeQueryClient()} />);
    await screen.findByRole("heading", { name: "Dashboard" });
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0];
    const expectations: [string, string][] = [
      ["Leads", "Leads"],
      ["Lists", "Lists & segments"],
      ["Lead discovery", "Lead discovery"],
      ["Campaigns", "Campaigns"],
      ["Email sequences", "Email sequences"],
      ["Templates", "Templates"],
      ["Inbox", "Inbox"],
      ["Sending accounts", "Sending accounts"],
      ["Analytics", "Analytics"],
      ["Suppression list", "Suppression list"],
      ["Settings", "Settings"],
      ["Dashboard", "Dashboard"],
    ];
    for (const [link, heading] of expectations) {
      await user.click(within(nav).getByRole("link", { name: new RegExp(`^${link}`) }));
      expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
    }
  });

  it("shows first-run setup when no password exists", async () => {
    setup({ authenticated: false, setup_required: true });
    render(<App client={makeQueryClient()} />);
    expect(await screen.findByText("First-run setup")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Password"), "short");
    await user.click(screen.getByRole("button", { name: "Create password" }));
    expect(await screen.findByText("Use at least 10 characters.")).toBeInTheDocument();
  });

  it("validates the lead form before submitting", async () => {
    setup();
    window.history.pushState({}, "", "/leads");
    const user = userEvent.setup();
    render(<App client={makeQueryClient()} />);
    await user.click(await screen.findByRole("button", { name: /New lead/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Email *"), "not-an-email");
    await user.click(within(dialog).getByRole("button", { name: "Create lead" }));
    expect(await within(dialog).findByText("Enter a valid email address")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.some(([u, init]) => u === "/api/leads" && (init as RequestInit)?.method === "POST")).toBe(false));
  });
});
