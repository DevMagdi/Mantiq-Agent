/** API payload types (mirror of the FastAPI response models that the UI consumes). */

export type Tenant = {
  id: string;
  name: string;
  slug: string;
  locale: string;
  timezone: string;
  currency: string;
  plan: string;
  kind: string;
  data_region: string;
};

export type User = {
  id: string;
  email: string;
  full_name: string;
  full_name_ar: string;
  roles: string[];
  is_superadmin: boolean;
  must_change_password: boolean;
  mfa_enabled: boolean;
  locale: string;
  preferred_currency: string;
  phone: string;
  job_title: string;
  tenant: Tenant;
  permissions?: string[];
};

export type LoginResult = {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
  mfa_required?: boolean;
  challenge_token?: string;
};

export type Page<T> = { items: T[]; total?: number; page?: number; page_size?: number };

export type RFQStatus = "draft" | "sent" | "replied" | "negotiating" | "closed" | "cancelled";

export type RFQSummary = {
  id: string;
  code: string;
  title: string;
  status: RFQStatus;
  mode: "draft_only" | "semi_auto" | "auto";
  currency: string;
  deadline: string | null;
  created_at?: string;
  buyer_id?: string;
  days_left?: number | null;
  offers_count?: number;
  suppliers_count?: number;
  estimated_value?: string | null;
  savings_pct?: number | null;
};

export type RFQLine = {
  id: string;
  line_no: number;
  description: string;
  quantity: string;
  uom: string;
  target_price: string | null;
  taxonomy_code?: string | null;
};

export type RFQSupplierLink = {
  invitation_id: string;
  supplier_id: string;
  name: string;
  name_ar?: string;
  channel: string;
  status: string;
  sent_at: string | null;
  reminder_count: number;
};

export type RFQMessage = {
  id: string;
  direction: "inbound" | "outbound";
  channel: string;
  status: string;
  body: string;
  author_type: string;
  created_at: string;
};

export type RFQDetail = RFQSummary & {
  incoterm: string;
  payment_terms: string;
  delivery_location: string;
  pr: { id: string; code: string; title: string } | null;
  lines: RFQLine[];
  suppliers: RFQSupplierLink[];
  offers: Offer[];
  messages: RFQMessage[];
  policy?: NegotiationPolicy | null;
  scoring_weights?: Record<string, number>;
};

export type Offer = {
  id: string;
  supplier_id: string;
  supplier_name: string;
  currency: string;
  subtotal: string;
  tco_total: string;
  landed_cost?: string;
  lead_time_days: number | null;
  validity_date: string | null;
  payment_terms?: string;
  incoterm?: string;
  status: string;
  confidence?: number;
  score?: number;
  rank?: number;
  score_breakdown?: Record<string, number>;
};

export type ComparisonCell = {
  offer_id: string;
  unit_price: string;
  line_total: string;
  currency: string;
  low_confidence: boolean;
  lead_time_days: number | null;
  extraction_id?: string;
  verified: boolean;
};

export type ComparisonRow = {
  rfq_line_id: string;
  line_no: number;
  description: string;
  quantity: string;
  uom: string;
  best_unit_price: string;
  target_price?: string | null;
  cells: ComparisonCell[];
};

export type ComparisonColumn = {
  offer_id: string;
  supplier_id: string;
  supplier_name: string;
  supplier_name_ar?: string;
  status: string;
  currency: string;
  incoterm?: string | null;
  payment_terms?: string | null;
  lead_time_days: number | null;
  validity_date: string | null;
  subtotal: string;
  landed_cost: string;
  tco_total: string;
  total_base?: string;
  fx_rate?: string;
  weighted_score: number;
  score_breakdown: Record<string, number>;
  rank: number;
  is_best_value: boolean;
  is_low_confidence: boolean;
  extraction_id?: string | null;
  supplier_quality?: number;
};

export type ComparisonRanking = {
  offer_id: string;
  supplier_name: string;
  tco_total: string;
  lead_time_days: number | null;
  supplier_quality: number;
  terms_score: number;
  risk_score: number;
  weighted_score: number;
  rank: number;
  score_breakdown: Record<string, number>;
};

export type Comparison = {
  rfq: { id: string; code: string; title: string; currency: string; status: string; weights: Record<string, number> };
  columns: ComparisonColumn[];
  rows: ComparisonRow[];
  ranking: ComparisonRanking[];
  blocked_for_low_confidence: boolean;
  generated_at?: string;
};

export type Recommendation = {
  recommended: ComparisonRanking[];
  justification_en: string;
  justification_ar: string;
  number_violations?: string[];
  matrix: Comparison;
};

export type NegotiationPolicy = {
  id?: string;
  target_price: string;
  walkaway_price: string;
  max_rounds: number;
  allowed_concessions: string[];
  mode: "draft_only" | "semi_auto" | "auto";
  value_threshold: string;
  tone?: string;
  currency?: string;
  version?: number;
};

export type NegotiationDraft = {
  message_id: string;
  status: string;
  body_en: string;
  body_ar?: string;
  proposed_price?: string;
  leak_violations?: string[];
  injection?: string[];
  market_hint_used?: Record<string, unknown> | null;
  decision: {
    allowed: boolean;
    action: string;
    codes: string[];
    reasons: string[];
    warnings: string[];
    proposed_price?: string | null;
  };
};

export type PRStatus = "draft" | "clarifying" | "ready" | "rfq_created" | "cancelled";

export type PurchaseRequest = {
  id: string;
  code: string;
  title: string;
  status: PRStatus;
  source: string;
  currency: string;
  total_estimate: string;
  confidence: number;
  needed_by: string | null;
  plant_id: string | null;
  clarification_resolved: boolean;
  created_at: string;
  lines?: PRLine[];
  clarifications?: Clarification[];
};

export type TaxonomyCandidate = {
  taxonomy_code: string;
  name: string;
  name_ar?: string;
  confidence: number;
  reason?: string;
};

export type PRLine = {
  id: string;
  line_no: number;
  description: string;
  quantity: string;
  uom: string;
  taxonomy_code: string | null;
  category_code?: string | null;
  target_price?: string | null;
  currency?: string | null;
  confidence?: number;
  normalization_confidence?: number;
  needs_review?: boolean;
  candidates?: TaxonomyCandidate[];
  quoted_text?: string;
};

export type Clarification = {
  id: string;
  field: string;
  question: string;
  question_ar?: string;
  answer?: string | null;
  status: "open" | "answered" | "waived";
};

export type Supplier = {
  id: string;
  name: string;
  name_ar: string;
  country: string;
  city: string;
  website?: string;
  status: string;
  categories: string[];
  overall_score: number;
  ratings?: { price: number; lead_time: number; quality: number; responsiveness: number };
  sanctions_status?: string;
  certifications?: { code: string; issuer?: string; expires_at?: string }[];
  flags?: { type: string; reason: string; at: string }[];
  created_at?: string;
};

export type Approval = {
  id: string;
  rfq_id: string;
  rfq_code: string;
  rfq_title: string;
  step_no: number;
  status: "pending" | "approved" | "rejected" | "revision_requested" | "queued";
  approver_role: string;
  amount?: string;
  currency?: string;
  due_at?: string | null;
  comment?: string;
  decided_at?: string | null;
  offer_id?: string | null;
  can_decide?: boolean;
  chain?: { step_no: number; role: string; status: string; decided_at?: string | null }[];
};

export type NotificationFeed = {
  items: Notification[];
  unread: number;
};

export type Notification = {
  id: string;
  kind: string;
  title: string;
  title_ar: string;
  body: string;
  link: string;
  read_at: string | null;
  created_at: string;
};

export type Kpis = {
  period_days: number;
  rfqs_total: number;
  rfqs_open: number;
  offers_total: number;
  purchase_orders: number;
  pending_approvals: number;
  savings: { initial: string; final: string; savings: number; savings_pct: number; savings_amount: string };
  cycle_time_days: number;
  supplier_response_rate?: number;
  active_suppliers?: number;
};

export type DashboardPayload = {
  kpis: Kpis;
  spend_by_category?: { category: string; amount: string }[];
  savings_by_month?: { period: string; baseline: string; actual: string; savings: string }[];
  recent_rfqs?: RFQSummary[];
  activity?: AgentRun[];
  approvals?: Approval[];
};

export type AgentRun = {
  id: string;
  kind: string;
  status: string;
  model: string;
  ref_type: string;
  ref_id: string;
  input_summary: string;
  output_summary: string;
  reasoning?: string;
  policy_decision?: Record<string, unknown>;
  tokens_in?: number;
  tokens_out?: number;
  cost_usd?: string;
  latency_ms?: number;
  created_at: string;
  steps?: AgentStep[];
};

export type AgentStep = {
  id: string;
  step_no: number;
  node: string;
  status: string;
  reasoning?: string;
  latency_ms?: number;
  created_at: string;
};

export type Extraction = {
  id: string;
  rfq_id: string;
  supplier_id: string;
  supplier_name: string;
  status: string;
  confidence: number;
  needs_review: boolean;
  issues: string[];
  source_kind: string;
  offer_id?: string | null;
  created_at: string;
  fields?: ExtractedField[];
  raw_text?: string;
};

export type ExtractedField = {
  id: string;
  name: string;
  label: string;
  value: string;
  currency?: string | null;
  confidence: number;
  source_span?: string;
  page?: number;
  low_confidence: boolean;
  verified_by?: string | null;
  verified_at?: string | null;
};

export type IntegrationFieldKind = "text" | "password" | "number" | "url" | "email" | "tel" | "select";

export type IntegrationField = {
  key: string;
  label_en: string;
  label_ar: string;
  kind: IntegrationFieldKind;
  required: boolean;
  secret: boolean;
  placeholder: string;
  help_en: string;
  help_ar: string;
  default: string;
  options: { value: string; label: string }[];
};

export type IntegrationProvider = { key: string; label: string; fields: IntegrationField[] };

export type IntegrationKind = {
  key: string;
  label_en: string;
  label_ar: string;
  description_en: string;
  description_ar: string;
  tenant_managed: boolean;
  providers: IntegrationProvider[];
};

/** live = a real provider answered; sandbox = platform fake; error = configured but unusable. */
export type IntegrationStatus = "live" | "sandbox" | "not_configured" | "error" | "disabled";

export type IntegrationItem = {
  kind: string;
  provider: string;
  provider_source: "tenant" | "platform";
  platform_provider: string;
  status: IntegrationStatus;
  enabled: boolean;
  tenant_managed: boolean;
  config: Record<string, string>;
  secrets_saved: string[];
  masked: Record<string, string>;
  missing: string[];
  last_tested_at: string | null;
  last_error: string;
  last_test_ok: boolean | null;
};

export type IntegrationProbe = {
  ok: boolean;
  status: IntegrationStatus;
  detail: string;
  latency_ms: number;
  probe: string;
};

export type IntegrationsPayload = {
  items: IntegrationItem[];
  catalogue: IntegrationKind[];
  platform_adapters: Record<string, string>;
  statuses: IntegrationStatus[];
};

export type ApiKey = {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  last_used_at?: string | null;
  revoked_at?: string | null;
  created_at: string;
};

export type Usage = {
  plan?: string;
  usage?: Record<string, number>;
  limits?: Record<string, number>;
  percent?: Record<string, number>;
  subscription?: { plan: string; status: string; seats: number; period_end: string };
  plan_price?: string;
  features?: string[];
};

export type DiscoveryCandidate = {
  id: string;
  name: string;
  name_ar?: string;
  website?: string;
  city?: string;
  country: string;
  confidence: number;
  source_url?: string;
  dedup_status?: string;
  status: string;
  contact_email?: string;
  evidence?: Record<string, unknown>;
};

export type DiscoveryJob = {
  job_id: string;
  status: string;
  provider: string;
  found: number;
  candidates: DiscoveryCandidate[];
};

export type Board = { columns: Record<string, RFQSummary[]> };

export type PublicForm = {
  rfq: { code: string; title: string; currency: string; deadline: string | null; incoterm: string; payment_terms: string };
  supplier: { name: string; name_ar: string };
  contact_name: string;
  lines: { id: string; line_no: number; description: string; quantity: string; uom: string }[];
  expires_at: string;
  fields: string[];
};

export type PublicPortal = {
  supplier: { name: string; name_ar: string; country: string; city: string; website: string; status: string; categories: string[] };
  contacts: { name: string; role: string; email: string; phone: string; is_primary: boolean }[];
  requested: string;
  expires_at: string;
  message: string;
};

/* ------------------------------------------------------------------ post-PO lifecycle */

export type PoStatus = "issued" | "in_transit" | "partially_received" | "received" | "confirmed" | "closed";

export type PurchaseOrderLine = {
  line_no: number;
  description: string;
  uom: string;
  quantity: string;
  unit_price: string;
  line_total: string;
  rfq_line_id?: string | null;
};

export type PurchaseOrderSummary = {
  id: string;
  code: string;
  rfq_id: string;
  supplier_id: string;
  supplier_name?: string;
  supplier_name_ar?: string;
  amount: string;
  currency: string;
  status: PoStatus;
  status_label: string;
  status_label_ar: string;
  issued_at: string | null;
  closed_at?: string | null;
  erp_status: string;
  lines: number;
};

export type ReceiptLine = {
  line_no: number;
  description: string;
  uom: string;
  ordered_qty: string;
  received_qty: string;
  accepted_qty: string;
  rejected_qty?: string;
  condition?: string;
  note?: string;
};

export type GoodsReceipt = {
  id: string;
  code: string;
  po_id: string;
  status: "received" | "confirmed" | "rejected";
  delivery_note_ref: string;
  carrier: string;
  warehouse: string;
  lines: ReceiptLine[];
  notes: string;
  received_by: string | null;
  received_at: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  confirmation_checks: Record<string, boolean>;
  confirmation_comment: string;
};

export type MatchIssue = {
  code: string;
  line_no: number;
  message: string;
  message_ar: string;
  detail?: string;
};

export type MatchLine = {
  line_no: number;
  description: string;
  uom: string;
  invoiced_qty: string;
  unit_price: string;
  line_total: string;
  ordered_qty: string;
  po_unit_price: string;
  received_qty: string;
  previously_invoiced_qty: string;
  available_qty?: string;
  price_variance_pct?: number;
  status: "ok" | "mismatch";
  issues: string[];
};

export type MatchResult = {
  status: "matched" | "mismatch" | "incomplete";
  issues: MatchIssue[];
  lines: MatchLine[];
  tolerance_pct: string;
  totals: { po_amount: string; invoiced_amount: string; received_value: string; currency: string };
  receipts_confirmed: boolean;
  can_approve: boolean;
  over_invoiced: boolean;
  checked_at: string;
};

export type PaymentReason = { code: string; text_en: string; text_ar: string };

export type PaymentSuggestion = {
  method: string;
  method_label_en: string;
  method_label_ar: string;
  terms: string;
  currency: string;
  amount_due: string;
  total: string;
  paid_amount: string;
  due_date: string | null;
  days_until_due: number | null;
  priority: "high" | "normal" | "low";
  cross_border: boolean;
  requires_fx: boolean;
  requires_human_approval: boolean;
  execution: "outside_platform";
  reasons: PaymentReason[];
};

export type SupplierInvoice = {
  id: string;
  number: string;
  po_id: string;
  supplier_id: string;
  status: "received" | "matched" | "mismatched" | "approved" | "partially_paid" | "paid" | "disputed";
  currency: string;
  subtotal: string;
  tax_amount: string;
  total: string;
  paid_amount: string;
  outstanding: string;
  invoice_date: string | null;
  due_date: string | null;
  lines: { line_no: number; description: string; uom: string; quantity: string; unit_price: string; line_total: string }[];
  match_result: Partial<MatchResult>;
  payment_suggestion: Partial<PaymentSuggestion>;
  approved_by: string | null;
  approved_at: string | null;
  notes: string;
};

export type LedgerEntry = {
  id: string;
  kind: "invoice" | "payment" | "credit_note" | "adjustment";
  amount: string;
  currency: string;
  entry_date: string;
  method: string;
  reference: string;
  invoice_id: string | null;
  po_id: string | null;
  note: string;
};

export type SupplierBalance = {
  supplier_id: string;
  invoiced_total: string;
  paid_total: string;
  outstanding: string;
  overdue: string;
  open_invoices: number;
  currency: string;
  last_payment_at: string | null;
  entries: LedgerEntry[];
  supplier_name?: string;
  supplier_name_ar?: string;
};

export type SupplierBalanceRow = {
  supplier_id: string;
  supplier_name: string;
  supplier_name_ar: string;
  status: string;
  balance: string;
  outstanding: string;
  overdue: string;
  open_invoices: number;
  currency: string;
  last_payment_at: string | null;
};

export type PurchaseOrderDetail = Omit<PurchaseOrderSummary, "lines"> & {
  lines: PurchaseOrderLine[];
  supplier: { id: string; name: string; name_ar: string; status: string } | null;
  rfq: { id: string; code: string; title: string; payment_terms: string; incoterm: string } | null;
  document_html: string;
  timeline: {
    kind: string;
    at: string | null;
    actor: string | null;
    label_en: string;
    label_ar: string;
    status: string;
    done: boolean;
    ref?: string;
  }[];
  receipts: GoodsReceipt[];
  invoices: SupplierInvoice[];
  discrepancy: {
    lines: {
      line_no: number;
      description: string;
      uom: string;
      ordered_qty: string;
      received_qty: string;
      confirmed_qty: string;
      outstanding_qty: string;
      status: string;
    }[];
    ordered_total: string;
    received_total: string;
    outstanding_lines: number;
    short_delivery: boolean;
  };
  /** Absent when the caller lacks `ledger:read`: commercial balances are financial data. */
  supplier_balance?: SupplierBalance | null;
  next_status: PoStatus | null;
  can_close: boolean;
};

/* ------------------------------------------------------------------ accounting (phase 2) */

export type GlAccount = {
  code: string;
  name: string;
  name_ar: string;
  kind: "asset" | "liability" | "equity" | "revenue" | "expense";
  normal_balance: "debit" | "credit";
  parent_code: string | null;
  is_control: boolean;
  is_active: boolean;
  description: string;
};

export type CostCenter = {
  code: string;
  name: string;
  name_ar: string;
  plant_id: string | null;
  manager_id: string | null;
  is_active: boolean;
  description: string;
};

export type JournalLineView = {
  line_no: number;
  account_code: string;
  account_name?: string;
  description: string;
  debit: string;
  credit: string;
  cost_center_code?: string | null;
  unit_number?: string | null;
  item_id?: string | null;
  supplier_id?: string | null;
  po_line_no?: number;
};

export type JournalEntryView = {
  id: string;
  number: string;
  entry_date: string;
  currency: string;
  memo: string;
  memo_ar: string;
  status: "draft" | "posted" | "reversed";
  status_label: string;
  status_label_ar: string;
  source_type: string;
  source_label: string;
  source_label_ar: string;
  source_id: string | null;
  po_id: string | null;
  invoice_id: string | null;
  receipt_id: string | null;
  supplier_id: string | null;
  total_debit: string;
  total_credit: string;
  balanced: boolean;
  created_by: string | null;
  created_at: string | null;
  posted_by: string | null;
  posted_at: string | null;
  reversal_of: string | null;
  reversed_by: string | null;
  reversal_reason: string;
  lines: JournalLineView[];
};

export type JournalList = {
  items: JournalEntryView[];
  total: number;
  posted: number;
  posted_total: string;
};

export type TrialBalance = {
  items: {
    code: string;
    account_code: string;
    account_name: string;
    account_name_ar: string;
    kind: string;
    debit: string;
    credit: string;
    balance: string;
  }[];
  total_debit: string;
  total_credit: string;
  balanced: boolean;
  currency: string;
};

export type CostReport = {
  group_by: "cost_center" | "item" | "unit" | "supplier" | "po";
  items: { key: string; label: string; label_ar: string; amount: string; share_pct: number }[];
  total: string;
  currency: string;
};

export type SupplierAccountView = {
  supplier_id: string;
  supplier_name: string;
  supplier_name_ar: string;
  payable: string;
  debit_total: string;
  credit_total: string;
  movements: {
    entry_id: string;
    number: string;
    entry_date: string;
    source_type: string;
    source_label: string;
    source_label_ar: string;
    description: string;
    debit: string;
    credit: string;
    invoice_id: string | null;
  }[];
  ledger_entries: {
    id: string;
    kind: string;
    amount: string;
    currency: string;
    entry_date: string;
    method: string;
    reference: string;
    invoice_id: string | null;
    po_id: string | null;
  }[];
  balance: {
    supplier_id: string;
    outstanding: string;
    overdue: string;
    invoiced_total: string;
    paid_total: string;
    currency: string;
    open_invoices: number;
  };
};

export type AuditPackageComponent = {
  kind: string;
  label_en: string;
  label_ar: string;
  ref_id: string | null;
  hash: string;
  payload?: Record<string, unknown>;
};

export type AuditPackageView = {
  id: string;
  code: string;
  po_id: string;
  invoice_id: string | null;
  status: "assembled" | "reviewed" | "queried";
  status_label: string;
  status_label_ar: string;
  manifest_hash: string;
  summary: Record<string, unknown>;
  components: AuditPackageComponent[];
  component_count: number;
  generated_by: string | null;
  generated_at: string | null;
  reviewer_id: string | null;
  reviewed_at: string | null;
  decision: "accepted" | "queried" | null;
  comment: string;
  can_post_to_accounts: boolean;
};

export type AuditPackageEnvelope = {
  po_id: string;
  package: AuditPackageView | null;
  status: string;
  can_post_to_accounts: boolean;
  components: AuditPackageComponent[];
  verification?: { intact: boolean; broken_components: string[]; manifest_hash: string };
  hint_en?: string;
  hint_ar?: string;
};

export type LifecycleStage = {
  code: string;
  label_en: string;
  label_ar: string;
  order: number;
  state: "done" | "current" | "pending";
  reached_at: string | null;
  actor_id: string | null;
};

export type Lifecycle = {
  po_id: string;
  current: string;
  current_label_en: string;
  current_label_ar: string;
  next_action_en: string;
  next_action_ar: string;
  stages: LifecycleStage[];
  history: { stage: string; reached_at: string; actor_id: string | null; source: string }[];
};

export type PoAccounting = {
  po_id: string;
  currency: string;
  entries: JournalEntryView[];
  draft_count: number;
  posted_count: number;
  cost_by_center: CostReport;
  accounts: { code: string; name: string; name_ar: string; kind: string }[];
};

/* ------------------------------------------------------------------ phase 3: channels, inbox, voice */
export type ChannelKey = "whatsapp" | "voice" | "email" | "web_form";

export type ChannelLabel = {
  channel: string;
  label: string;
  label_ar: string;
  enabled?: boolean;
  primary?: boolean;
};

export type FeatureFlag = {
  key: string;
  enabled: boolean;
  label_en: string;
  label_ar: string;
  description_en?: string;
  description_ar?: string;
  global_enabled?: boolean;
  default?: boolean;
};

export type ChannelState = ChannelLabel & {
  available: boolean;
  opted_in: boolean;
  reasons: string[];
  address_hint: string;
  has_address: boolean;
};

/** What the backend would actually use for this supplier right now, and why. */
export type ResolvedChannel = {
  supplier_id: string;
  supplier_name: string;
  supplier_name_ar: string;
  channel: string;
  channel_label: string;
  channel_label_ar: string;
  source: string;
  source_label: string;
  source_label_ar: string;
  available: boolean;
  opted_in: boolean;
  reasons: string[];
  candidates: ChannelState[];
  rfq_id: string | null;
  invitation_id: string | null;
};

export type ChannelCapabilities = {
  channels: ChannelLabel[];
  default_order: string[];
  whatsapp: {
    provider: string;
    supports_templates: boolean;
    supports_documents: boolean;
    supports_interactive: boolean;
    supports_read_receipts: boolean;
  };
  features: FeatureFlag[];
};

export type SupplierChannels = {
  supplier_id: string;
  preferred_channel: string;
  secondary_channels: string[];
  preferred_language: string;
  channels: ChannelState[];
  resolved: ResolvedChannel;
};

export type RfqChannelRow = {
  supplier_id: string;
  supplier_name: string;
  supplier_name_ar: string;
  channel: string;
  channel_label: string;
  channel_label_ar: string;
  source: string;
  source_label: string;
  source_label_ar: string;
  available: boolean;
  opted_in: boolean;
  reasons: string[];
  comm_status: string;
  comm_status_label: string;
  comm_status_label_ar: string;
  invitations: number;
  replies: number;
  last_message_at: string | null;
  delivered_at: string | null;
  read_at: string | null;
  channel_override: string | null;
};

export type RfqChannels = {
  rfq_id: string;
  rfq_code: string;
  items: RfqChannelRow[];
  channels_in_use: string[];
  mixed_channels: boolean;
};

export type InboxMessage = {
  id: string;
  direction: "inbound" | "outbound";
  channel: string;
  channel_label: string;
  channel_label_ar: string;
  author_type: string;
  author_id: string | null;
  status: string;
  delivery_status: string;
  delivered_at: string | null;
  read_at: string | null;
  subject: string;
  body: string;
  body_ar: string;
  media_type: string;
  template_code: string;
  locale: string;
  created_at: string;
};

export type InboxThread = {
  thread_id: string;
  rfq_id: string;
  rfq_code: string;
  rfq_title: string;
  supplier_id: string;
  supplier_name: string;
  supplier_name_ar: string;
  current_channel: string;
  channels_used: string[];
  last_message: InboxMessage;
  last_message_at: string | null;
  message_count: number;
  inbound_count: number;
  outbound_count: number;
  comm_status: string;
  comm_status_label: string;
  comm_status_label_ar: string;
  awaiting_reply: boolean;
  preferred_channel: string;
};

export type InboxList = {
  items: InboxThread[];
  total: number;
  channels: string[];
  awaiting_reply: number;
  generated_at: string;
};

export type InboxThreadDetail = {
  thread_id: string;
  rfq_id: string;
  rfq_code: string;
  rfq_title: string;
  supplier_id: string;
  supplier_name: string;
  supplier_name_ar: string;
  current_channel: string;
  reply_channel: string;
  channels_used: string[];
  messages: InboxMessage[];
  events: ChannelEventView[];
  resolved_channel: {
    channel: string;
    channel_label: string;
    channel_label_ar: string;
    available: boolean;
    reasons: string[];
  } | null;
  comm_status: string;
};

export type ChannelEventView = {
  id: string;
  kind: string;
  channel: string;
  supplier_id?: string | null;
  actor_type: string;
  actor_id: string | null;
  detail: Record<string, unknown>;
  created_at: string | null;
};

export type MessageTemplateView = {
  id: string;
  code: string;
  kind: string;
  channel: string;
  locale: string;
  name: string;
  subject: string;
  body: string;
  whatsapp_template_name: string;
  version: number;
  description: string;
  variables: string[];
};

export type TemplateLibrary = {
  channels: { channel: string; label: string; label_ar: string; count: number }[];
  items: Record<string, MessageTemplateView[]>;
  total: number;
};

export type VoiceCallRow = {
  id: string;
  rfq_id: string;
  supplier_id: string;
  status: string;
  disclosure_played: boolean;
  consent_given: boolean;
  recording: boolean;
  duration_seconds: number;
  extracted_offer_id: string | null;
  blocked_turns: number;
  created_at: string | null;
};

export type VoiceTurn = {
  seq: number;
  speaker: string;
  text: string;
  text_ar?: string;
  topic: string;
  decision: string;
  block_code: string;
  reason: string;
};

export type VoiceCallDetail = {
  call_id: string;
  rfq_id: string;
  rfq_code: string;
  supplier_id: string;
  supplier_name: string;
  status: string;
  language: string;
  disclosure_played: boolean;
  consent_given: boolean;
  recording: boolean;
  duration_seconds: number;
  transcript: string;
  summary: string;
  turns: VoiceTurn[];
  policy_snapshot: {
    ai_agent?: {
      knowledge_version: string;
      topics_answered: string[];
      topics_blocked: string[];
      blocked_turns: number;
      guard: string;
    };
  };
  policy: { allowed_topics: string[]; blocked_topics: string[] };
  extraction_id: string | null;
  created_at: string;
};

export type AssistantAnswer = {
  ok: boolean;
  session_id?: string;
  intent: string;
  channel?: string;
  locale?: string;
  answer_en: string;
  answer_ar: string;
  data: Record<string, unknown>;
  citations: { type: string; id: string; code: string; label: string; link: string }[];
  blocked_code: string;
  can_act: false;
  requires?: string | null;
  asked_at: string;
  error?: string;
  codes?: string[];
};

export type AssistantSessionRow = {
  session_id: string;
  channel: string;
  locale: string;
  opened_at: string;
  first_question: string;
};

export type AssistantTurnView = {
  seq: number;
  role: "user" | "assistant";
  text: string;
  text_ar: string;
  intent: string;
  answer: Record<string, unknown>;
  citations: { type: string; id: string; code: string; label: string; link: string }[];
  blocked_code: string;
  at: string | null;
};

export type AssistantSessionDetail = {
  session_id: string;
  channel: string;
  locale: string;
  status: string;
  user_id?: string;
  roles: string[];
  permission_snapshot: string[];
  started_at: string | null;
  turns: AssistantTurnView[];
};

export type VoiceFeatures = {
  features: FeatureFlag[];
  provider: string;
  disclosure_required: boolean;
  recording_requires_consent: boolean;
};
