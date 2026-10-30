/**
 * Permission names used by the UI.
 *
 * They mirror `backend/app/core/rbac.py`; a typo here would silently hide a screen, so the
 * strings live in one place and are only referenced through this map.
 */
export const PERM = {
  // requisitions & sourcing
  prRead: "pr:read",
  prCreate: "pr:create",
  rfqRead: "rfq:read",
  rfqSend: "rfq:send",
  supplierRead: "supplier:read",
  comparisonRead: "comparison:read",
  negotiationDraft: "negotiation:draft",
  // governance
  approvalRead: "approval:read",
  approvalDecide: "approval:decide",
  auditRead: "audit:read",
  // post-award
  poRead: "po:read",
  poUpdate: "po:update",
  poExport: "po:export",
  receiptCreate: "receipt:create",
  receiptConfirm: "receipt:confirm",
  invoiceCreate: "invoice:create",
  invoiceApprove: "invoice:approve",
  paymentRecord: "payment:record",
  ledgerRead: "ledger:read",
  // accounting & analytics (phase 2)
  accountingRead: "accounting:read",
  accountingWrite: "accounting:write",
  accountingPost: "accounting:post",
  accountingReverse: "accounting:reverse",
  costRead: "cost:read",
  costWrite: "cost:write",
  auditPackageRead: "audit_package:read",
  auditPackageReview: "audit_package:review",
  // channels & voice (phase 3)
  channelRead: "channel:read",
  channelWrite: "channel:write",
  inboxRead: "inbox:read",
  inboxReply: "inbox:reply",
  assistantUse: "assistant:use",
  voiceCall: "voice:call",
  // reporting & admin
  reportRead: "report:read",
  settingsRead: "settings:read",
  settingsUpdate: "settings:update",
  adminIntegrations: "admin:integrations",
  adminApiKeys: "admin:api_keys",
  adminLlm: "admin:llm",
  adminBilling: "admin:billing",
} as const;

export type PermissionName = (typeof PERM)[keyof typeof PERM];
