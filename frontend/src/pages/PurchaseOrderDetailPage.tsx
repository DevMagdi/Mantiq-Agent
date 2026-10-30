import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BadgeCheck,
  Banknote,
  Check,
  CheckCircle2,
  ClipboardCheck,
  FileText,
  PackageCheck,
  RefreshCw,
  Ship,
  ShieldCheck,
  Truck,
  XCircle,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { PageHeading } from "@/components/Shell";
import {
  Badge,
  Card,
  CardHeader,
  Drawer,
  EmptyState,
  ErrorState,
  LoadingBlock,
  Spinner,
  StatusChip,
  cn,
  useToast,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, dateTime, money, num } from "@/lib/format";
import { useAuth } from "@/lib/session";
import type { AppLang } from "@/i18n";
import type {
  GoodsReceipt,
  MatchResult,
  PurchaseOrderDetail,
  SupplierInvoice,
} from "@/lib/types";

const STEPS = ["issued", "in_transit", "received", "confirmed", "closed"] as const;

function stepIndex(status: PurchaseOrderDetail["status"]): number {
  if (status === "partially_received") return 2;
  return STEPS.indexOf(status as (typeof STEPS)[number]);
}

export default function PurchaseOrderDetailPage() {
  const { id = "" } = useParams();
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [receiptOpen, setReceiptOpen] = useState(false);
  const [confirmTarget, setConfirmTarget] = useState<GoodsReceipt | null>(null);
  const [invoiceOpen, setInvoiceOpen] = useState(false);
  const [approveTarget, setApproveTarget] = useState<SupplierInvoice | null>(null);
  const [payTarget, setPayTarget] = useState<SupplierInvoice | null>(null);

  const query = useQuery({
    queryKey: ["purchase-order", id],
    queryFn: () => api.get<PurchaseOrderDetail>(`/api/v1/purchase-orders/${id}`),
    enabled: Boolean(id),
  });

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["purchase-order", id] });
    await queryClient.invalidateQueries({ queryKey: ["purchase-orders"] });
    await queryClient.invalidateQueries({ queryKey: ["supplier-balances"] });
  };

  const ship = useMutation({
    mutationFn: () => api.post(`/api/v1/purchase-orders/${id}/ship?carrier=${encodeURIComponent("")}`),
    onSuccess: async () => {
      toast.push(t("purchaseOrders.toast.shipped"), "ok");
      await invalidate();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const close = useMutation({
    mutationFn: () => api.post(`/api/v1/purchase-orders/${id}/close`),
    onSuccess: async () => {
      toast.push(t("purchaseOrders.toast.closed"), "ok");
      await invalidate();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const rematch = useMutation({
    mutationFn: (invoiceId: string) => api.post(`/api/v1/invoices/${invoiceId}/match`),
    onSuccess: async () => {
      await invalidate();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  if (query.isLoading) return <LoadingBlock rows={8} />;
  if (query.error || !query.data) {
    return <ErrorState message={errorMessage(query.error)} onRetry={() => void query.refetch()} />;
  }

  const po = query.data;
  const activeStep = stepIndex(po.status);
  const payableInvoices = po.invoices.filter((row) => ["matched", "mismatched"].includes(row.status));

  return (
    <div className="space-y-5">
      <PageHeading
        eyebrow={po.rfq ? `${po.rfq.code} · ${po.rfq.title}` : undefined}
        title={po.code}
        subtitle={`${lang === "ar" && po.supplier?.name_ar ? po.supplier.name_ar : po.supplier?.name ?? ""} · ${money(
          po.amount,
          po.currency,
          lang,
        )}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip status={po.status} label={lang === "ar" ? po.status_label_ar : po.status_label} />
            {can("po:update") && po.status === "issued" ? (
              <button type="button" className="btn-secondary btn-sm" onClick={() => ship.mutate()} disabled={ship.isPending}>
                {ship.isPending ? <Spinner /> : <Ship className="size-3.5" aria-hidden />}
                {t("purchaseOrders.actions.ship")}
              </button>
            ) : null}
            {can("receipt:create") && po.status !== "closed" ? (
              <button type="button" className="btn-secondary btn-sm" onClick={() => setReceiptOpen(true)}>
                <PackageCheck className="size-3.5" aria-hidden />
                {t("purchaseOrders.actions.receive")}
              </button>
            ) : null}
            {can("invoice:create") && po.status !== "closed" ? (
              <button type="button" className="btn-secondary btn-sm" onClick={() => setInvoiceOpen(true)}>
                <FileText className="size-3.5" aria-hidden />
                {t("purchaseOrders.actions.invoice")}
              </button>
            ) : null}
            {can("po:update") && po.can_close ? (
              <button type="button" className="btn-primary btn-sm" onClick={() => close.mutate()} disabled={close.isPending}>
                {close.isPending ? <Spinner /> : <CheckCircle2 className="size-3.5" aria-hidden />}
                {t("purchaseOrders.actions.close")}
              </button>
            ) : null}
            {can("po:export") ? (
              <>
                <button type="button" className="btn-ghost btn-sm"
                        onClick={() => void api.download(`/api/v1/purchase-orders/${po.id}/pdf`, `${po.code}.pdf`)}>
                  {t("purchaseOrders.actions.pdf")}
                </button>
                <button type="button" className="btn-ghost btn-sm"
                        onClick={() => void api.download(`/api/v1/purchase-orders/${po.id}/excel`, `${po.code}.xlsx`)}>
                  {t("purchaseOrders.actions.excel")}
                </button>
              </>
            ) : null}
          </div>
        }
      />

      <LifecycleStepper po={po} activeStep={activeStep} />

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card className="overflow-hidden">
            <CardHeader
              title={t("purchaseOrders.lines")}
              subtitle={`${t("purchaseOrders.match.ordered")} ${money(po.discrepancy.ordered_total, po.currency, lang)} · ${t(
                "purchaseOrders.match.received",
              )} ${money(po.discrepancy.received_total, po.currency, lang)}`}
              action={po.discrepancy.short_delivery ? <Badge tone="warn">{t("purchaseOrders.statuses.partially_received")}</Badge> : <Badge tone="ok">{t("purchaseOrders.match.state.ok")}</Badge>}
            />
            <table className="table-dense">
              <thead>
                <tr>
                  <th>{t("purchaseOrders.match.line")}</th>
                  <th className="text-end">{t("purchaseOrders.match.ordered")}</th>
                  <th className="text-end">{t("purchaseOrders.match.received")}</th>
                  <th className="text-end">{t("purchaseOrders.match.available")}</th>
                  <th className="text-end">{t("purchaseOrders.match.unitPrice")}</th>
                  <th className="text-end">{t("common.total")}</th>
                </tr>
              </thead>
              <tbody>
                {po.discrepancy.lines.map((line) => (
                  <tr key={line.line_no}>
                    <td className="max-w-[22rem]">
                      <span className="block truncate text-[0.8125rem] text-ink">{line.description}</span>
                      <span className="text-2xs text-ink-subtle">{line.uom}</span>
                    </td>
                    <td className="tabular text-end text-[0.8125rem]">{num(line.ordered_qty, lang, 2)}</td>
                    <td className="tabular text-end text-[0.8125rem]">{num(line.received_qty, lang, 2)}</td>
                    <td
                      className={cn(
                        "tabular text-end text-[0.8125rem]",
                        line.status === "outstanding" ? "text-[color:var(--warn-ink)]" : "text-ink-muted",
                      )}
                    >
                      {num(line.outstanding_qty, lang, 2)}
                    </td>
                    <td className="tabular text-end text-[0.8125rem]">
                      {money(po.lines.find((row) => row.line_no === line.line_no)?.unit_price ?? "0", po.currency, lang)}
                    </td>
                    <td className="tabular text-end text-[0.8125rem]">
                      {money(po.lines.find((row) => row.line_no === line.line_no)?.line_total ?? "0", po.currency, lang)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <Card>
            <CardHeader
              title={t("purchaseOrders.receipts.title")}
              subtitle={t("purchaseOrders.receipts.subtitle")}
              action={<span className="text-2xs text-ink-subtle">{t("purchaseOrders.receipts.confirmationNote")}</span>}
            />
            {po.receipts.length === 0 ? (
              <EmptyState title={t("purchaseOrders.receipts.empty")} icon={<Truck className="size-5" />} />
            ) : (
              <ul className="divide-y divide-line/70">
                {po.receipts.map((receipt) => (
                  <li key={receipt.id} className="px-4 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <p className="text-[0.8125rem] font-semibold text-ink">{receipt.code}</p>
                        <p className="text-2xs text-ink-subtle">
                          {[receipt.warehouse, receipt.carrier, receipt.delivery_note_ref].filter(Boolean).join(" · ")}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <StatusChip status={receipt.status} />
                        <span className="text-2xs text-ink-muted">{dateTime(receipt.received_at, lang)}</span>
                        {receipt.status === "received" && can("receipt:confirm") ? (
                          <button type="button" className="btn-primary btn-sm" onClick={() => setConfirmTarget(receipt)}>
                            <ClipboardCheck className="size-3.5" aria-hidden />
                            {t("purchaseOrders.actions.confirm")}
                          </button>
                        ) : null}
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-ink-muted">
                      {receipt.lines.map((line) => (
                        <span key={line.line_no} className="tabular">
                          #{line.line_no} · {num(line.accepted_qty, lang, 2)} / {num(line.ordered_qty, lang, 2)}{" "}
                          {line.uom}
                        </span>
                      ))}
                    </div>
                    {receipt.confirmation_comment ? (
                      <p className="mt-2 rounded-md border border-line/70 bg-surface-raised/40 px-2 py-1 text-2xs text-ink-muted">
                        {receipt.confirmation_comment}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader title={t("purchaseOrders.invoices.title")} subtitle={t("purchaseOrders.invoices.subtitle")} />
            {po.invoices.length === 0 ? (
              <EmptyState title={t("purchaseOrders.invoices.empty")} icon={<FileText className="size-5" />} />
            ) : (
              <ul className="divide-y divide-line/70">
                {po.invoices.map((invoice) => (
                  <li key={invoice.id} className="px-4 py-3">
                    <InvoiceRow
                      invoice={invoice}
                      lang={lang}
                      canApprove={can("invoice:approve")}
                      canPay={can("payment:record")}
                      onApprove={() => setApproveTarget(invoice)}
                      onPay={() => setPayTarget(invoice)}
                      onRematch={() => rematch.mutate(invoice.id)}
                      busy={rematch.isPending && rematch.variables === invoice.id}
                      payable={payableInvoices.some((row) => row.id === invoice.id)}
                    />
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader title={t("purchaseOrders.balance.title")} subtitle={t("purchaseOrders.balance.derived")} />
            <div className="grid grid-cols-2 gap-3 p-4">
              <Figure label={t("purchaseOrders.balance.outstanding")}
                      value={money(po.supplier_balance.outstanding, po.currency, lang)} tone="gold" />
              <Figure label={t("purchaseOrders.balance.overdue")}
                      value={money(po.supplier_balance.overdue, po.currency, lang)}
                      tone={Number(po.supplier_balance.overdue) > 0 ? "danger" : "muted"} />
              <Figure label={t("purchaseOrders.balance.invoiced")}
                      value={money(po.supplier_balance.invoiced_total, po.currency, lang)} />
              <Figure label={t("purchaseOrders.balance.paid")}
                      value={money(po.supplier_balance.paid_total, po.currency, lang)} />
            </div>
            <div className="border-t border-line px-4 py-3">
              <p className="eyebrow mb-2">{t("purchaseOrders.balance.entries")}</p>
              {po.supplier_balance.entries.length === 0 ? (
                <p className="text-2xs text-ink-subtle">{t("purchaseOrders.balance.empty")}</p>
              ) : (
                <ul className="space-y-1.5">
                  {po.supplier_balance.entries.slice(0, 6).map((entry) => (
                    <li key={entry.id} className="flex items-center justify-between gap-2 text-2xs">
                      <span className="text-ink-muted">
                        {entry.kind === "payment" ? t("purchaseOrders.balance.paid") : t("purchaseOrders.balance.invoiced")}
                        {entry.reference ? ` · ${entry.reference}` : ""}
                      </span>
                      <span className={cn("tabular", Number(entry.amount) < 0 ? "text-[color:var(--ok-ink)]" : "text-ink")}>
                        {money(entry.amount, entry.currency, lang)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title={t("purchaseOrders.lifecycle")} />
            <ol className="space-y-3 p-4">
              {po.timeline.map((event, index) => (
                <li key={`${event.kind}-${index}`} className="flex gap-3">
                  <span className="mt-1 flex size-6 shrink-0 items-center justify-center rounded-full border border-gold/40 bg-gold/10 text-gold">
                    <Check className="size-3" aria-hidden />
                  </span>
                  <div className="min-w-0">
                    <p className="text-2xs font-medium text-ink">
                      {lang === "ar" ? event.label_ar : event.label_en}
                    </p>
                    <p className="text-3xs text-ink-subtle">{dateTime(event.at, lang)}</p>
                  </div>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      </div>

      <ReceiptDrawer
        open={receiptOpen}
        onClose={() => setReceiptOpen(false)}
        po={po}
        onDone={async () => {
          setReceiptOpen(false);
          toast.push(t("purchaseOrders.toast.received"), "ok");
          await invalidate();
        }}
      />
      <ConfirmDrawer
        receipt={confirmTarget}
        onClose={() => setConfirmTarget(null)}
        onDone={async (message) => {
          setConfirmTarget(null);
          toast.push(message, "ok");
          await invalidate();
        }}
      />
      <InvoiceDrawer
        open={invoiceOpen}
        onClose={() => setInvoiceOpen(false)}
        po={po}
        onDone={async () => {
          setInvoiceOpen(false);
          toast.push(t("purchaseOrders.toast.invoiced"), "ok");
          await invalidate();
        }}
      />
      <ApproveDrawer
        invoice={approveTarget}
        onClose={() => setApproveTarget(null)}
        onDone={async () => {
          setApproveTarget(null);
          toast.push(t("purchaseOrders.toast.approved"), "ok");
          await invalidate();
        }}
      />
      <PaymentDrawer
        invoice={payTarget}
        onClose={() => setPayTarget(null)}
        onDone={async () => {
          setPayTarget(null);
          toast.push(t("purchaseOrders.toast.paid"), "ok");
          await invalidate();
        }}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

function Figure({ label, value, tone = "muted" }: { label: string; value: string; tone?: "gold" | "danger" | "muted" }) {
  return (
    <div>
      <p className="text-2xs text-ink-subtle">{label}</p>
      <p
        className={cn(
          "mt-0.5 tabular text-sm font-semibold",
          tone === "gold" ? "text-[color:var(--gold-300)]" : tone === "danger" ? "text-[color:var(--danger-ink)]" : "text-ink",
        )}
      >
        {value}
      </p>
    </div>
  );
}

function LifecycleStepper({ po, activeStep }: { po: PurchaseOrderDetail; activeStep: number }) {
  const { t } = useTranslation();
  return (
    <Card className="px-4 py-3">
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-2">
        {STEPS.map((step, index) => {
          const done = index <= activeStep;
          const current = index === activeStep;
          return (
            <li key={step} className="flex items-center gap-2">
              <span
                className={cn(
                  "stepper-dot",
                  done
                    ? "border-gold/60 bg-gold/15 text-[color:var(--gold-300)]"
                    : "border-line bg-surface-raised text-ink-subtle",
                  current && "ring-2 ring-[color:var(--focus-ring)]",
                )}
                aria-current={current ? "step" : undefined}
              >
                {done ? <Check className="size-3.5" aria-hidden /> : index + 1}
              </span>
              <span className={cn("text-2xs", done ? "text-ink" : "text-ink-subtle")}>
                {t(`purchaseOrders.steps.${step}`)}
              </span>
              {index < STEPS.length - 1 ? (
                <span className={cn("h-px w-6", index < activeStep ? "bg-gold/50" : "bg-line")} aria-hidden />
              ) : null}
            </li>
          );
        })}
        <li className="ms-auto text-2xs text-ink-subtle">
          {po.next_status ? `${t("common.next")}: ${t(`purchaseOrders.statuses.${po.next_status}`)}` : null}
        </li>
      </ol>
    </Card>
  );
}

function InvoiceRow({
  invoice,
  lang,
  canApprove,
  canPay,
  onApprove,
  onPay,
  onRematch,
  busy,
  payable,
}: {
  invoice: SupplierInvoice;
  lang: AppLang;
  canApprove: boolean;
  canPay: boolean;
  onApprove: () => void;
  onPay: () => void;
  onRematch: () => void;
  busy: boolean;
  payable: boolean;
}) {
  const { t } = useTranslation();
  const match = invoice.match_result as Partial<MatchResult>;
  const suggestion = invoice.payment_suggestion;
  const tone = match.status === "matched" ? "ok" : match.status === "mismatch" ? "danger" : "warn";
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[0.8125rem] font-semibold text-ink">{invoice.number}</p>
          <p className="text-2xs text-ink-subtle">
            {money(invoice.total, invoice.currency, lang)} · {t("purchaseOrders.invoices.due")}:{" "}
            {invoice.due_date ? date(invoice.due_date, lang) : t("common.none")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={tone}>{match.status ? t(`purchaseOrders.invoices.${match.status === "matched" ? "matched" : match.status === "mismatch" ? "mismatch" : "incomplete"}`) : "—"}</Badge>
          <StatusChip status={invoice.status} />
          <button type="button" className="btn-ghost btn-sm" onClick={onRematch} disabled={busy} title={t("purchaseOrders.actions.rematch")}>
            {busy ? <Spinner /> : <RefreshCw className="size-3.5" aria-hidden />}
          </button>
          {canApprove && payable ? (
            <button type="button" className="btn-secondary btn-sm" onClick={onApprove}>
              <ShieldCheck className="size-3.5" aria-hidden />
              {t("purchaseOrders.actions.approveInvoice")}
            </button>
          ) : null}
          {canPay && ["approved", "partially_paid"].includes(invoice.status) ? (
            <button type="button" className="btn-primary btn-sm" onClick={onPay}>
              <Banknote className="size-3.5" aria-hidden />
              {t("purchaseOrders.actions.pay")}
            </button>
          ) : null}
        </div>
      </div>

      {match.lines && match.lines.length > 0 ? (
        <div className="overflow-x-auto rounded-md border border-line/70">
          <table className="table-dense">
            <thead>
              <tr>
                <th>{t("purchaseOrders.match.line")}</th>
                <th className="text-end">{t("purchaseOrders.match.received")}</th>
                <th className="text-end">{t("purchaseOrders.match.invoiced")}</th>
                <th className="text-end">{t("purchaseOrders.match.available")}</th>
                <th className="text-end">{t("purchaseOrders.match.variance")}</th>
                <th>{t("purchaseOrders.match.state.ok")}</th>
              </tr>
            </thead>
            <tbody>
              {match.lines.map((line) => (
                <tr key={line.line_no}>
                  <td className="max-w-[16rem] truncate text-2xs text-ink-muted">#{line.line_no} {line.description}</td>
                  <td className="tabular text-end text-2xs">{num(line.received_qty, lang, 2)}</td>
                  <td className="tabular text-end text-2xs">{num(line.invoiced_qty, lang, 2)}</td>
                  <td className="tabular text-end text-2xs">{line.available_qty ? num(line.available_qty, lang, 2) : "—"}</td>
                  <td
                    className={cn(
                      "tabular text-end text-2xs",
                      line.price_variance_pct && Math.abs(line.price_variance_pct) > 0.5
                        ? "text-[color:var(--danger-ink)]"
                        : "text-ink-muted",
                    )}
                  >
                    {line.price_variance_pct === undefined ? "—" : `${line.price_variance_pct.toFixed(2)}%`}
                  </td>
                  <td>
                    {line.status === "ok" ? (
                      <BadgeCheck className="size-4 text-[color:var(--ok-ink)]" aria-label={t("purchaseOrders.match.state.ok")} />
                    ) : (
                      <XCircle className="size-4 text-[color:var(--danger-ink)]" aria-label={t("purchaseOrders.match.state.mismatch")} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {match.issues && match.issues.length > 0 ? (
        <div className="rounded-md border border-[color:var(--danger)]/30 bg-[color:var(--danger)]/10 px-3 py-2">
          <p className="text-2xs font-semibold text-[color:var(--danger-ink)]">{t("purchaseOrders.invoices.mismatchTitle")}</p>
          <ul className="mt-1 space-y-0.5">
            {match.issues.map((issue, index) => (
              <li key={`${issue.code}-${index}`} className="text-2xs text-ink-muted">
                {lang === "ar" ? issue.message_ar : issue.message}
                {issue.line_no ? ` (#${issue.line_no})` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : match.status === "matched" ? (
        <p className="text-2xs text-[color:var(--ok-ink)]">{t("purchaseOrders.invoices.noIssues")}</p>
      ) : null}

      {suggestion?.method ? (
        <p className="text-2xs text-ink-subtle">
          {t("purchaseOrders.payment.title")}: {lang === "ar" ? suggestion.method_label_ar : suggestion.method_label_en} ·{" "}
          {suggestion.terms} · {money(suggestion.amount_due ?? "0", invoice.currency, lang)}
        </p>
      ) : null}
    </div>
  );
}

function ReceiptDrawer({
  open,
  onClose,
  po,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  po: PurchaseOrderDetail;
  onDone: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const [draft, setDraft] = useState<Record<number, { received: string; accepted: string; rejected: string }>>({});
  const [meta, setMeta] = useState({ warehouse: "", carrier: "", delivery_note_ref: "", notes: "" });

  const outstanding = useMemo(
    () =>
      po.discrepancy.lines.filter((line) => Number(line.outstanding_qty) > 0),
    [po],
  );

  const value = (lineNo: number, key: "received" | "accepted" | "rejected") => {
    const row = draft[lineNo];
    if (row) return row[key];
    const line = outstanding.find((item) => item.line_no === lineNo);
    if (!line) return "0";
    return key === "rejected" ? "0" : line.outstanding_qty;
  };

  const setValue = (lineNo: number, key: "received" | "accepted" | "rejected", next: string) => {
    const line = outstanding.find((item) => item.line_no === lineNo);
    const current = draft[lineNo] ?? {
      received: line?.outstanding_qty ?? "0",
      accepted: line?.outstanding_qty ?? "0",
      rejected: "0",
    };
    setDraft({ ...draft, [lineNo]: { ...current, [key]: next } });
  };

  const submit = useMutation({
    mutationFn: () =>
      api.post(`/api/v1/purchase-orders/${po.id}/receipts`, {
        lines: outstanding.map((line) => ({
          line_no: line.line_no,
          received_qty: value(line.line_no, "received"),
          accepted_qty: value(line.line_no, "accepted"),
          rejected_qty: value(line.line_no, "rejected"),
        })),
        ...meta,
      }),
    onSuccess: async () => {
      setDraft({});
      setMeta({ warehouse: "", carrier: "", delivery_note_ref: "", notes: "" });
      await onDone();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={t("purchaseOrders.receipt.title")}
      description={t("purchaseOrders.receipt.hint")}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={() => submit.mutate()}
            disabled={submit.isPending || outstanding.length === 0}
          >
            {submit.isPending ? <Spinner /> : <PackageCheck className="size-4" aria-hidden />}
            {t("purchaseOrders.receipt.submit")}
          </button>
        </div>
      }
    >
      {outstanding.length === 0 ? (
        <EmptyState title={t("purchaseOrders.receipts.empty")} />
      ) : (
        <div className="space-y-4">
          <table className="table-dense">
            <thead>
              <tr>
                <th>{t("purchaseOrders.match.line")}</th>
                <th className="text-end">{t("purchaseOrders.receipt.receivedQty")}</th>
                <th className="text-end">{t("purchaseOrders.receipt.acceptedQty")}</th>
                <th className="text-end">{t("purchaseOrders.receipt.rejectedQty")}</th>
              </tr>
            </thead>
            <tbody>
              {outstanding.map((line) => (
                <tr key={line.line_no}>
                  <td className="max-w-[13rem]">
                    <span className="block truncate text-2xs text-ink">{line.description}</span>
                    <span className="text-3xs text-ink-subtle">
                      {t("purchaseOrders.match.available")}: {num(line.outstanding_qty, lang, 2)} {line.uom}
                    </span>
                  </td>
                  {(["received", "accepted", "rejected"] as const).map((key) => (
                    <td key={key} className="text-end">
                      <input
                        className="input w-24 text-end"
                        inputMode="decimal"
                        aria-label={`${line.description} ${key}`}
                        value={value(line.line_no, key)}
                        onChange={(event) => setValue(line.line_no, key, event.target.value)}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="label">{t("purchaseOrders.receipts.warehouse")}</span>
              <input className="input" value={meta.warehouse} onChange={(event) => setMeta({ ...meta, warehouse: event.target.value })} />
            </label>
            <label className="block">
              <span className="label">{t("purchaseOrders.receipts.carrier")}</span>
              <input className="input" value={meta.carrier} onChange={(event) => setMeta({ ...meta, carrier: event.target.value })} />
            </label>
            <label className="block">
              <span className="label">{t("purchaseOrders.receipts.deliveryNote")}</span>
              <input
                className="input"
                value={meta.delivery_note_ref}
                onChange={(event) => setMeta({ ...meta, delivery_note_ref: event.target.value })}
              />
            </label>
            <label className="block">
              <span className="label">{t("common.notes")}</span>
              <input className="input" value={meta.notes} onChange={(event) => setMeta({ ...meta, notes: event.target.value })} />
            </label>
          </div>
        </div>
      )}
    </Drawer>
  );
}

function ConfirmDrawer({
  receipt,
  onClose,
  onDone,
}: {
  receipt: GoodsReceipt | null;
  onClose: () => void;
  onDone: (message: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const [checks, setChecks] = useState({ quantity_ok: true, quality_ok: true, spec_ok: true, documents_ok: false });
  const [comment, setComment] = useState("");

  const decide = useMutation({
    mutationFn: (decision: "confirm" | "reject") =>
      api.post(`/api/v1/receipts/${receipt?.id}/confirm`, { decision, ...checks, comment }),
    onSuccess: async (_data, decision) => {
      setComment("");
      await onDone(decision === "confirm" ? t("purchaseOrders.toast.confirmed") : t("purchaseOrders.toast.rejected"));
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  return (
    <Drawer
      open={Boolean(receipt)}
      onClose={onClose}
      title={`${t("purchaseOrders.actions.confirm")} · ${receipt?.code ?? ""}`}
      description={t("purchaseOrders.receipts.confirmationNote")}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="btn-danger"
            onClick={() => decide.mutate("reject")}
            disabled={decide.isPending || !comment.trim()}
          >
            {t("purchaseOrders.actions.reject")}
          </button>
          <button type="button" className="btn-primary" onClick={() => decide.mutate("confirm")} disabled={decide.isPending}>
            {decide.isPending ? <Spinner /> : <CheckCircle2 className="size-4" aria-hidden />}
            {t("purchaseOrders.actions.confirm")}
          </button>
        </div>
      }
    >
      <div className="space-y-3">
        {(
          [
            ["quantity_ok", t("purchaseOrders.receipts.checks.quantity")],
            ["quality_ok", t("purchaseOrders.receipts.checks.quality")],
            ["spec_ok", t("purchaseOrders.receipts.checks.spec")],
            ["documents_ok", t("purchaseOrders.receipts.checks.documents")],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              className="size-4 accent-[color:var(--gold)]"
              checked={checks[key]}
              onChange={(event) => setChecks({ ...checks, [key]: event.target.checked })}
            />
            {label}
          </label>
        ))}
        <label className="block">
          <span className="label">{t("purchaseOrders.receipts.comment")}</span>
          <textarea
            className="input min-h-24"
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder={t("purchaseOrders.receipts.comment")}
          />
        </label>
      </div>
    </Drawer>
  );
}

function InvoiceDrawer({
  open,
  onClose,
  po,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  po: PurchaseOrderDetail;
  onDone: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const [number, setNumber] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [tax, setTax] = useState("0");
  const [quantities, setQuantities] = useState<Record<number, string>>({});

  const confirmed = po.discrepancy.lines.filter((line) => Number(line.confirmed_qty) > 0);
  const qty = (lineNo: number) => {
    if (quantities[lineNo] !== undefined) return quantities[lineNo];
    const line = confirmed.find((row) => row.line_no === lineNo);
    return line?.confirmed_qty ?? "0";
  };

  const submit = useMutation({
    mutationFn: () =>
      api.post(`/api/v1/purchase-orders/${po.id}/invoices`, {
        number,
        due_date: dueDate ? new Date(dueDate).toISOString() : null,
        tax_amount: tax || "0",
        lines: confirmed.map((line) => ({
          line_no: line.line_no,
          description: line.description,
          uom: line.uom,
          quantity: qty(line.line_no),
          unit_price: po.lines.find((row) => row.line_no === line.line_no)?.unit_price ?? "0",
        })),
      }),
    onSuccess: async () => {
      setNumber("");
      setDueDate("");
      setTax("0");
      setQuantities({});
      await onDone();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={t("purchaseOrders.invoices.addTitle")}
      description={t("purchaseOrders.invoices.addHint")}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={() => submit.mutate()}
            disabled={submit.isPending || !number.trim() || confirmed.length === 0}
          >
            {submit.isPending ? <Spinner /> : <FileText className="size-4" aria-hidden />}
            {t("purchaseOrders.invoices.submit")}
          </button>
        </div>
      }
    >
      {confirmed.length === 0 ? (
        <EmptyState title={t("purchaseOrders.invoices.empty")} description={t("purchaseOrders.receipts.confirmationNote")} />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="label">{t("purchaseOrders.invoices.number")}</span>
              <input className="input" value={number} onChange={(event) => setNumber(event.target.value)} />
            </label>
            <label className="block">
              <span className="label">{t("purchaseOrders.invoices.due")}</span>
              <input type="date" className="input" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
            </label>
            <label className="block">
              <span className="label">{t("purchaseOrders.invoices.tax")}</span>
              <input className="input" inputMode="decimal" value={tax} onChange={(event) => setTax(event.target.value)} />
            </label>
          </div>

          <table className="table-dense">
            <thead>
              <tr>
                <th>{t("purchaseOrders.match.line")}</th>
                <th className="text-end">{t("purchaseOrders.match.available")}</th>
                <th className="text-end">{t("purchaseOrders.match.invoiced")}</th>
                <th className="text-end">{t("purchaseOrders.match.unitPrice")}</th>
              </tr>
            </thead>
            <tbody>
              {confirmed.map((line) => (
                <tr key={line.line_no}>
                  <td className="max-w-[13rem] truncate text-2xs text-ink">{line.description}</td>
                  <td className="tabular text-end text-2xs">{num(line.confirmed_qty, lang, 2)} {line.uom}</td>
                  <td className="text-end">
                    <input
                      className="input w-24 text-end"
                      inputMode="decimal"
                      aria-label={`${line.description} ${t("purchaseOrders.match.invoiced")}`}
                      value={qty(line.line_no)}
                      onChange={(event) => setQuantities({ ...quantities, [line.line_no]: event.target.value })}
                    />
                  </td>
                  <td className="tabular text-end text-2xs">
                    {money(po.lines.find((row) => row.line_no === line.line_no)?.unit_price ?? "0", po.currency, lang)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Drawer>
  );
}

function ApproveDrawer({
  invoice,
  onClose,
  onDone,
}: {
  invoice: SupplierInvoice | null;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [override, setOverride] = useState("");
  const needsOverride = invoice?.match_result?.can_approve === false;

  const approve = useMutation({
    mutationFn: () =>
      api.post(`/api/v1/invoices/${invoice?.id}/approve`, { comment, override_reason: override }),
    onSuccess: async () => {
      setComment("");
      setOverride("");
      await onDone();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  return (
    <Drawer
      open={Boolean(invoice)}
      onClose={onClose}
      title={`${t("purchaseOrders.invoices.approveTitle")} · ${invoice?.number ?? ""}`}
      description={t("purchaseOrders.invoices.approveHint")}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={() => approve.mutate()}
            disabled={approve.isPending || (needsOverride && !override.trim())}
          >
            {approve.isPending ? <Spinner /> : <ShieldCheck className="size-4" aria-hidden />}
            {t("purchaseOrders.actions.approveInvoice")}
          </button>
        </div>
      }
    >
      <div className="space-y-3">
        <p className="text-sm text-ink">
          {money(invoice?.outstanding ?? "0", invoice?.currency ?? "EGP", "en")} · {invoice?.status}
        </p>
        {needsOverride ? (
          <label className="block">
            <span className="label">{t("purchaseOrders.invoices.override")}</span>
            <textarea
              className="input min-h-20"
              value={override}
              onChange={(event) => setOverride(event.target.value)}
              placeholder={t("purchaseOrders.invoices.overrideHint")}
            />
            <span className="mt-1 block text-2xs text-ink-subtle">{t("purchaseOrders.invoices.overrideHint")}</span>
          </label>
        ) : null}
        <label className="block">
          <span className="label">{t("common.notes")}</span>
          <textarea className="input min-h-20" value={comment} onChange={(event) => setComment(event.target.value)} />
        </label>
        <p className="rounded-md border border-line bg-surface-raised/50 px-3 py-2 text-2xs text-ink-muted">
          {t("purchaseOrders.payment.human")}
        </p>
      </div>
    </Drawer>
  );
}

function PaymentDrawer({
  invoice,
  onClose,
  onDone,
}: {
  invoice: SupplierInvoice | null;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const suggestion = invoice?.payment_suggestion;
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");

  const effectiveAmount = amount || invoice?.outstanding || "0";
  const effectiveMethod = method || suggestion?.method || "bank_transfer";

  const pay = useMutation({
    mutationFn: () =>
      api.post(`/api/v1/invoices/${invoice?.id}/payments`, {
        amount: effectiveAmount,
        method: effectiveMethod,
        reference,
        note,
      }),
    onSuccess: async () => {
      setAmount("");
      setReference("");
      setNote("");
      await onDone();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  return (
    <Drawer
      open={Boolean(invoice)}
      onClose={onClose}
      title={`${t("purchaseOrders.payment.recordTitle")} · ${invoice?.number ?? ""}`}
      description={t("purchaseOrders.payment.recordHint")}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={() => pay.mutate()}
            disabled={pay.isPending || !reference.trim()}
          >
            {pay.isPending ? <Spinner /> : <Banknote className="size-4" aria-hidden />}
            {t("purchaseOrders.payment.submit")}
          </button>
        </div>
      }
    >
      <div className="space-y-3">
        {suggestion?.method ? (
          <div className="rounded-md border border-line bg-surface-raised/50 px-3 py-2">
            <p className="text-2xs font-semibold text-ink">
              {t("purchaseOrders.payment.title")}: {suggestion.method_label_en} · {suggestion.terms}
            </p>
            <ul className="mt-1 space-y-0.5">
              {(suggestion.reasons ?? []).map((reason) => (
                <li key={reason.code} className="text-2xs text-ink-muted">
                  {reason.text_en}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <label className="block">
          <span className="label">{t("purchaseOrders.payment.amountDue")}</span>
          <input className="input" inputMode="decimal" value={effectiveAmount} onChange={(event) => setAmount(event.target.value)} />
        </label>
        <label className="block">
          <span className="label">{t("purchaseOrders.payment.method")}</span>
          <select className="input" value={effectiveMethod} onChange={(event) => setMethod(event.target.value)}>
            {["bank_transfer", "swift_wire", "letter_of_credit", "cheque"].map((option) => (
              <option key={option} value={option}>
                {option.replaceAll("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">{t("purchaseOrders.payment.reference")}</span>
          <input className="input" value={reference} onChange={(event) => setReference(event.target.value)} />
        </label>
        <label className="block">
          <span className="label">{t("common.notes")}</span>
          <textarea className="input min-h-20" value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
      </div>
    </Drawer>
  );
}
