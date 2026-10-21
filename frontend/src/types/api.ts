export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface CampaignRef {
  id: number;
  name: string;
  status: string;
  recipient_status: string;
}

export interface Lead {
  id: number;
  email: string;
  first_name: string | null;
  last_name: string | null;
  full_name: string | null;
  job_title: string | null;
  company: string | null;
  company_id: number | null;
  company_website: string | null;
  linkedin_url: string | null;
  industry: string | null;
  country: string | null;
  timezone: string | null;
  source: string | null;
  notes: string | null;
  custom_fields: Record<string, string | number | boolean | null>;
  tags: string[];
  status: string;
  suppressed: boolean;
  campaigns: CampaignRef[];
  created_at: string;
  updated_at: string;
  last_contacted_at: string | null;
  last_replied_at: string | null;
}

export interface LeadList {
  id: number;
  name: string;
  description: string | null;
  lead_count: number;
  created_at: string;
}

export interface SegmentRule {
  field: string;
  op: string;
  value: unknown;
}

export interface Segment {
  id: number;
  name: string;
  description: string | null;
  match: "all" | "any";
  rules: SegmentRule[];
  lead_count: number;
  created_at: string;
}

export interface Account {
  id: number;
  display_name: string;
  email: string;
  reply_to: string | null;
  provider: "smtp_imap" | "gmail_oauth" | "microsoft_oauth";
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_security: string;
  smtp_username: string | null;
  imap_enabled: boolean;
  imap_host: string | null;
  imap_port: number | null;
  imap_security: string;
  imap_username: string | null;
  imap_folder: string;
  daily_limit: number;
  hourly_limit: number;
  min_delay_seconds: number;
  status: string;
  status_message: string | null;
  sending_enabled: boolean;
  last_test_at: string | null;
  last_send_at: string | null;
  last_sync_at: string | null;
  created_at: string;
  has_smtp_password: boolean;
  has_imap_password: boolean;
  has_oauth_token: boolean;
  sent_last_24h: number;
}

export interface TemplateVersion {
  id: number;
  version: number;
  subject: string;
  body: string;
  format: "text" | "html";
  variables: string[];
  note: string | null;
  created_at: string;
}

export interface Template {
  id: number;
  name: string;
  description: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
  current: TemplateVersion | null;
  version_count: number;
}

export interface Preview {
  subject: string | null;
  text: string | null;
  html: string | null;
  missing: string[];
  errors: string[];
  variables: string[];
  unknown_variables: string[];
  lead_id: number | null;
  sample_data: boolean;
}

export interface SequenceStep {
  id?: number;
  position?: number;
  template_id: number;
  template_name?: string | null;
  delay_days: number;
  delay_hours: number;
  send_as_reply: boolean;
}

export interface Sequence {
  id: number;
  name: string;
  description: string | null;
  steps: SequenceStep[];
  created_at: string;
  campaign_count: number;
}

export interface Campaign {
  id: number;
  name: string;
  description: string | null;
  status: string;
  email_account_id: number | null;
  sequence_id: number | null;
  timezone: string;
  window_start: string;
  window_end: string;
  send_days: number[];
  daily_limit: number;
  min_delay_seconds: number;
  start_at: string | null;
  end_at: string | null;
  max_emails_per_recipient: number;
  exclusion_rules: { exclude_domains?: string[]; skip_if_contacted_within_days?: number | null; exclude_active_in_other_campaigns?: boolean };
  dry_run: boolean;
  approved_at: string | null;
  approval_summary: Record<string, unknown> | null;
  activated_at: string | null;
  paused_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  created_at: string;
  updated_at: string;
  list_ids: number[];
  segment_ids: number[];
  recipient_counts: Record<string, number>;
  step_count: number;
}

export interface CampaignReview {
  campaign_id: number;
  status: string;
  mode: "dry_run" | "live";
  candidates: number;
  duplicates_across_sources: number;
  eligible: number;
  eligible_sample: { lead_id: number; email: string }[];
  excluded: Record<string, number>;
  missing_fields: { variable: string; count: number }[];
  missing_samples: { lead_id: number; email: string; missing: string[] }[];
  account: { id: number; email: string; status: string; sending_enabled: boolean; live_ready: boolean; daily_limit: number; hourly_limit: number } | null;
  steps: { step_number: number; template_id: number | null; template_name?: string; version: number | null; subject: string | null; delay_days: number; delay_hours: number; send_as_reply: boolean }[];
  limits: Record<string, number | null>;
  window: { timezone: string; start: string; end: string; days: number[] };
  estimated_schedule: { date: string; emails: number }[];
  estimated_initial_send_days: number;
  problems: string[];
  warnings: string[];
}

export interface Recipient {
  id: number;
  lead_id: number;
  email: string;
  name: string | null;
  company: string | null;
  status: string;
  steps_sent: number;
  next_step: number;
  next_send_at: string | null;
  last_sent_at: string | null;
  stop_reason: string | null;
}

export interface ConversationSummary {
  id: number;
  subject: string | null;
  status: string;
  classification: string | null;
  classification_source: string | null;
  needs_review: boolean;
  labels: string[];
  last_message_at: string;
  lead: { id: number; email: string; name: string | null } | null;
  campaign: { id: number; name: string } | null;
  snippet: string | null;
  from_email: string | null;
  is_auto_reply: boolean | null;
}

export interface ConversationMessage {
  id: number;
  direction: "inbound" | "outbound";
  from_email: string | null;
  from_name: string | null;
  to_email: string | null;
  subject: string | null;
  body_text: string | null;
  snippet: string | null;
  occurred_at: string;
  is_auto_reply: boolean;
  is_simulation: boolean;
  match_method: string | null;
  message_id_header: string;
  classifications: { label: string; source: string; confidence: number; rationale: string | null }[];
}

export interface ConversationDetail extends ConversationSummary {
  notes: string | null;
  suppressed: boolean;
  messages: ConversationMessage[];
}

export interface Suppression {
  id: number;
  email: string | null;
  domain: string | null;
  reason: string;
  source: string;
  notes: string | null;
  active: boolean;
  created_at: string;
  lifted_at: string | null;
  lifted_reason: string | null;
}

export interface AppConfig {
  sender_name: string;
  default_sender_email: string | null;
  reply_to_email: string | null;
  company_name: string;
  company_website: string;
  mailing_address: string;
  signature: string;
  default_timezone: string;
  default_window_start: string;
  default_window_end: string;
  default_send_days: number[];
  daily_sending_limit: number;
  min_delay_seconds: number;
  followup_default_delay_days: number;
  max_followups: number;
  dry_run: boolean;
  emergency_stop: boolean;
  append_opt_out_footer: boolean;
  opt_out_text: string;
  auto_suppress_unsubscribe_replies: boolean;
  stop_on_out_of_office: boolean;
  ai_provider: string;
  ai_base_url: string;
  ai_model: string;
  ai_classify_replies: boolean;
  retention_inbound_body_days: number;
  retention_outbound_body_days: number;
  retention_audit_days: number;
}

export interface AiStatus {
  provider: string;
  model: string | null;
  base_url: string | null;
  has_api_key: boolean;
  ready: boolean;
  ai_enabled: boolean;
}

export interface SettingsResponse {
  config: AppConfig;
  ai: AiStatus;
  environment: { app_env: string; public_base_url: string | null; unsubscribe_links: boolean; database: string; bind_host: string; cookie_secure: boolean };
  live_sending_phrase: string;
  safety_fields: string[];
}

export interface AiDraft {
  id: number;
  lead_id: number | null;
  parent_id: number | null;
  label: string | null;
  provider: string;
  inputs: Record<string, unknown>;
  output: {
    subject_lines: string[];
    opening_lines: string[];
    body: string;
    short_version: string;
    long_version: string;
    follow_ups: { subject: string; body: string }[];
    segment_variations: { segment: string; body: string }[];
    grounding: string[];
    insufficient_context: boolean;
    notes: string;
    warnings: string[];
  };
  subject: string;
  body: string;
  status: "draft" | "approved";
  approved_at: string | null;
  created_at: string;
  versions?: { id: number; label: string | null; subject: string; status: string; created_at: string }[];
}

export interface MetricSummary {
  range: { start: string; end: string };
  metrics: Record<string, number | null>;
  definitions: Record<string, string>;
}

// --- Lead discovery ------------------------------------------------------------------------------

export interface DiscoveryReason {
  type: string;
  reason: string;
  evidence?: string | null;
  score?: number | null;
}

export interface DiscoverySignal {
  id: number;
  source_provider: string;
  source_type: string;
  source_url: string | null;
  category: string | null;
  title: string | null;
  company_name: string | null;
  company_domain: string | null;
  contact_name: string | null;
  contact_title: string | null;
  lead_id: number | null;
  published_at: string | null;
  discovered_at: string;
  excerpt: string | null;
  relevance_score: number | null;
  qualification_status: string;
  processing_status: string;
  error_code: string | null;
  error_detail: string | null;
  test_mode: boolean;
  draft: { id: number; status: string; subject: string } | null;
  reasons: DiscoveryReason[];
}

export interface DiscoveryEvidence {
  kind: string;
  url: string | null;
  text: string;
  title?: string;
  retrieved_at?: string;
  provider?: string;
  matched_keywords?: string[];
  error?: string;
}

export interface DiscoverySignalDetail extends DiscoverySignal {
  evidence: DiscoveryEvidence[];
  score_components: Record<string, { weight: number; score: number | null; reason: string; evidence: string | null }>;
  lead: { id: number; email: string; name: string | null; status: string } | null;
  drafts: { id: number; status: string; subject: string; label: string | null; created_at: string }[];
}

export interface DiscoveryRun {
  id: number;
  trigger: string;
  status: string;
  test_mode: boolean;
  started_at: string;
  finished_at: string | null;
  stats: Record<string, number>;
  errors: { source: string; code: string; message: string }[];
}

export interface DiscoveryStatus {
  enabled: boolean;
  test_mode: boolean;
  draft_generation_enabled: boolean;
  interval_minutes: number;
  last_run: DiscoveryRun | null;
  last_successful_run_at: string | null;
  next_run_at: string | null;
  signals_total: number;
  by_processing_status: Record<string, number>;
  by_qualification: Record<string, number>;
  qualified: number;
  drafts_created: number;
  drafts_awaiting_review: number;
  duplicates_skipped: number;
  failed_jobs: number;
  queued_jobs: number;
  warnings: string[];
}

export interface DiscoveryProvider {
  name: string;
  label: string;
  requires_key: boolean;
  cost: string;
  configured: boolean;
  key_source: "stored" | "environment" | null;
  enabled: boolean;
  usage: { requests_today: number; credits_today: number; errors_today: number; last_success_at: string | null; last_error_at: string | null; last_error: string | null };
  feeds?: number;
  watch_domains?: number;
  model?: string;
  active_for_drafting?: boolean;
  engine_id_configured?: boolean;
  enrichment_enabled?: boolean;
  credit_budget?: number;
  credits_used_today?: number;
}

export interface DiscoveryConfig {
  enabled: boolean;
  test_mode: boolean;
  draft_generation_enabled: boolean;
  target_industries: string[];
  company_sizes: string[];
  countries: string[];
  regions: string[];
  job_titles: string[];
  buyer_roles: string[];
  keywords: string[];
  excluded_keywords: string[];
  signal_categories: string[];
  excluded_domains: string[];
  excluded_companies: string[];
  max_results_per_run: number;
  interval_minutes: number;
  daily_limit: number;
  min_relevance_score: number;
  review_threshold: number;
  skip_if_contacted_within_days: number;
  weights: Record<string, number>;
  enabled_sources: string[];
  feeds: { url: string; category: string | null; company_name: string | null; company_domain: string | null }[];
  watch_domains: string[];
  max_pages_per_domain: number;
  search_queries: string[];
  enrichment_enabled: boolean;
  enrichment_confirmed: boolean;
  enrichment_daily_credit_budget: number;
  ai_daily_limit: number;
  ai_per_run_limit: number;
  max_evidence_chars: number;
  offer: { product_description: string; value_proposition: string; call_to_action: string; target_audience: string; tone: string; length: string };
  evidence_retention_days: number;
}

export interface DiscoveryDraft {
  id: number;
  status: string;
  subject: string;
  body: string;
  label: string | null;
  provider: string;
  created_at: string;
  warnings: string[];
  evidence_refs: { url: string | null; kind: string; retrieved_at: string | null; provider: string | null }[];
  test_mode: boolean;
  lead: { id: number; email: string; name: string | null; company: string | null } | null;
  signal: DiscoverySignal | null;
}
