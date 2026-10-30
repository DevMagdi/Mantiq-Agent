import { useQuery } from "@tanstack/react-query";
import { ArrowRight, PackageCheck, Truck, Wallet } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { PageHeading } from "@/components/Shell";
import { Card, EmptyState, ErrorState, Kpi, LoadingBlock, StatusChip, cn } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, money, num } from "@/lib/format";
import { useAuth } from "@/lib/session";
import { PERM } from "@/lib/permissions";
import type { AppLang } from "@/i18n";
import type { Page, PoStatus, PurchaseOrderSummary, SupplierBalanceRow } from "@/lib/types";

const FILTERS: { value: "all" | PoStatus; labelKey: string }[] = [
  { value: "all", labelKey: "common.all" },
  { value: "issued", labelKey: "purchaseOrders.statuses.issued" },
  { value: "in_transit", labelKey: "purchaseOrders.statuses.in_transit" },
  { value: "partially_received", labelKey: "purchaseOrders.statuses.partially_received" },
  { value: "received", labelKey: "purchaseOrders.statuses.received" },
  { value: "confirmed", labelKey: "purchaseOrders.statuses.confirmed" },
  { value: "closed", labelKey: "purchaseOrders.statuses.closed" },
];

export default function PurchaseOrdersPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const { can } = useAuth();
  const [status, setStatus] = useState<"all" | PoStatus>("all");

  const orders = useQuery({
    queryKey: ["purchase-orders", status],
    queryFn: () =>
      api.get<Page<PurchaseOrderSummary>>(
        `/api/v1/purchase-orders?page_size=100${status === "all" ? "" : `&status=${status}`}`,
      ),
  });

  const canSeeBalances = can(PERM.ledgerRead);
  const balances = useQuery({
    queryKey: ["supplier-balances"],
    queryFn: () => api.get<{ items: SupplierBalanceRow[]; total_outstanding: string; total_overdue: string }>(
      "/api/v1/supplier-balances",
    ),
    enabled: can(PERM.ledgerRead),
  });

  const items = useMemo(() => orders.data?.items ?? [], [orders.data]);
  const open = items.filter((row) => row.status !== "closed");
  const awaitingReceipt = items.filter((row) => row.status === "issued" || row.status === "in_transit");
  const awaitingConfirmation = items.filter((row) => row.status === "received");

  if (orders.isLoading) return <LoadingBlock rows={8} />;
  if (orders.error) return <ErrorState message={errorMessage(orders.error)} onRetry={() => void orders.refetch()} />;

  return (
    <div className="space-y-5">
      <PageHeading
        title={t("purchaseOrders.title")}
        subtitle={t("purchaseOrders.subtitle")}
        eyebrow={t("nav.groups.fulfilment")}
        actions={
          <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-surface-alt p-1">
            {FILTERS.map((filter) => (
              <button
                key={filter.value}
                type="button"
                onClick={() => setStatus(filter.value)}
                aria-pressed={status === filter.value}
                className={cn(
                  "rounded-md px-2.5 py-1 text-2xs font-medium transition-colors",
                  status === filter.value
                    ? "bg-gold/15 text-[color:var(--gold-300)]"
                    : "text-ink-muted hover:bg-surface-raised hover:text-ink",
                )}
              >
                {t(filter.labelKey)}
              </button>
            ))}
          </div>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label={t("purchaseOrders.openOrders")} value={num(open.length, lang)} icon={<PackageCheck className="size-4" />} />
        <Kpi label={t("purchaseOrders.awaitingReceipt")} value={num(awaitingReceipt.length, lang)} icon={<Truck className="size-4" />} />
        <Kpi
          label={t("purchaseOrders.awaitingConfirmation")}
          value={num(awaitingConfirmation.length, lang)}
          hint={t("purchaseOrders.receipts.pending")}
        />
        {canSeeBalances ? (
          <Kpi
            label={t("purchaseOrders.outstanding")}
            value={money(balances.data?.total_outstanding ?? "0", "EGP", lang)}
            hint={
              Number(balances.data?.total_overdue ?? 0) > 0
                ? `${t("purchaseOrders.balance.overdue")}: ${money(balances.data?.total_overdue, "EGP", lang)}`
                : t("purchaseOrders.balance.derived")
            }
            icon={<Wallet className="size-4" />}
          />
        ) : null}
      </div>

      <Card className="overflow-hidden">
        {items.length === 0 ? (
          <EmptyState title={t("purchaseOrders.empty")} description={t("purchaseOrders.emptyHint")}
                      icon={<PackageCheck className="size-5" />} />
        ) : (
          <div className="overflow-x-auto">
            <table className="table-dense">
              <thead>
                <tr>
                  <th>{t("purchaseOrders.supplier")}</th>
                  <th>{t("purchaseOrders.amount")}</th>
                  <th>{t("purchaseOrders.status")}</th>
                  <th>{t("purchaseOrders.issued")}</th>
                  <th className="text-end">{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((order) => (
                  <tr key={order.id} className="group">
                    <td>
                      <Link to={`/purchase-orders/${order.id}`} className="block">
                        <span className="text-[0.8125rem] font-semibold text-ink group-hover:text-gold">
                          {order.code}
                        </span>
                        <span className="block text-2xs text-ink-subtle">
                          {lang === "ar" && order.supplier_name_ar ? order.supplier_name_ar : order.supplier_name}
                        </span>
                      </Link>
                    </td>
                    <td className="tabular text-[0.8125rem] text-ink">{money(order.amount, order.currency, lang)}</td>
                    <td>
                      <StatusChip status={order.status}
                                  label={lang === "ar" ? order.status_label_ar : order.status_label} />
                    </td>
                    <td className="text-2xs text-ink-muted">{date(order.issued_at, lang)}</td>
                    <td className="text-end">
                      <Link
                        to={`/purchase-orders/${order.id}`}
                        className="inline-flex items-center gap-1 text-2xs font-medium text-gold hover:underline"
                      >
                        {t("common.details")}
                        <ArrowRight className="size-3 rtl:rotate-180" aria-hidden />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {can("ledger:read") && (balances.data?.items.length ?? 0) > 0 ? (
        <Card>
          <div className="card-header">
            <div>
              <h2 className="text-sm font-semibold text-ink">{t("purchaseOrders.balance.title")}</h2>
              <p className="mt-0.5 text-2xs text-ink-muted">{t("purchaseOrders.balance.derived")}</p>
            </div>
          </div>
          <table className="table-dense">
            <thead>
              <tr>
                <th>{t("common.supplier")}</th>
                <th className="text-end">{t("purchaseOrders.balance.outstanding")}</th>
                <th className="text-end">{t("purchaseOrders.balance.overdue")}</th>
                <th className="text-end">{t("purchaseOrders.balance.openInvoices")}</th>
              </tr>
            </thead>
            <tbody>
              {balances.data?.items.slice(0, 6).map((row) => (
                <tr key={row.supplier_id}>
                  <td className="text-[0.8125rem] text-ink">
                    {lang === "ar" && row.supplier_name_ar ? row.supplier_name_ar : row.supplier_name}
                  </td>
                  <td className="tabular text-end text-[0.8125rem] text-ink">
                    {money(row.outstanding, row.currency || "EGP", lang)}
                  </td>
                  <td className={cn("tabular text-end text-[0.8125rem]",
                                    Number(row.overdue) > 0 ? "text-[color:var(--danger-ink)]" : "text-ink-muted")}>
                    {money(row.overdue, row.currency || "EGP", lang)}
                  </td>
                  <td className="tabular text-end text-[0.8125rem] text-ink-muted">{num(row.open_invoices, lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}
    </div>
  );
}
