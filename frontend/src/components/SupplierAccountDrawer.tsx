import { useQuery } from "@tanstack/react-query";
import { BookOpen, Landmark, Receipt, Wallet } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  Badge,
  Drawer,
  EmptyState,
  ErrorState,
  Kpi,
  LoadingBlock,
  StatusChip,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, money } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { SupplierAccountView } from "@/lib/types";

/**
 * The supplier sub-ledger, read-only.
 *
 * Two independent views of the same supplier: the **journal movements** (the accounts-payable lines
 * actually booked by posted entries) and the **payment ledger** (invoices, payments and notes
 * recorded against the supplier). Both are read through `GET /accounting/supplier-account/{id}`,
 * which needs `ledger:read`; a payment method shown here is a suggestion, never an execution.
 */
export function SupplierAccountDrawer({
  supplierId,
  supplierName,
  onClose,
}: {
  supplierId: string | null;
  supplierName?: string;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;

  const query = useQuery({
    queryKey: ["supplier-account", supplierId],
    queryFn: () => api.get<SupplierAccountView>(`/api/v1/accounting/supplier-account/${supplierId}`),
    enabled: Boolean(supplierId),
  });

  const data = query.data;
  const name = data ? (lang === "ar" && data.supplier_name_ar ? data.supplier_name_ar : data.supplier_name) : (supplierName ?? "");
  const currency = data?.balance.currency || "EGP";

  return (
    <Drawer
      open={Boolean(supplierId)}
      onClose={onClose}
      title={t("accounting.supplier.title")}
      description={name}
      width="max-w-3xl"
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-md text-2xs text-ink-subtle">{t("accounting.supplier.footer")}</p>
          <button type="button" className="btn-ghost btn-sm" onClick={onClose}>
            {t("accounting.supplier.close")}
          </button>
        </div>
      }
    >
      {query.isLoading ? (
        <LoadingBlock />
      ) : query.isError ? (
        <ErrorState message={errorMessage(query.error)} onRetry={() => void query.refetch()} />
      ) : !data ? null : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Kpi
              label={t("accounting.supplier.payable")}
              icon={<Wallet className="size-3.5" />}
              value={money(data.balance.outstanding, currency, lang)}
              hint={t("accounting.supplier.payableHint")}
            />
            <Kpi
              label={t("accounting.supplier.overdue")}
              icon={<Landmark className="size-3.5" />}
              value={money(data.balance.overdue, currency, lang)}
              hint={t("accounting.supplier.overdueHint")}
            />
            <Kpi
              label={t("accounting.supplier.invoiced")}
              icon={<Receipt className="size-3.5" />}
              value={money(data.balance.invoiced_total, currency, lang)}
              hint={t("accounting.supplier.invoicedHint")}
            />
            <Kpi
              label={t("accounting.supplier.paid")}
              icon={<BookOpen className="size-3.5" />}
              value={money(data.balance.paid_total, currency, lang)}
              hint={t("accounting.supplier.paidHint")}
            />
          </div>

          <section className="rounded border border-line bg-surface">
            <header className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
              <div>
                <h3 className="text-sm font-semibold text-ink">{t("accounting.supplier.movements")}</h3>
                <p className="text-2xs text-ink-subtle">{t("accounting.supplier.movementsHint")}</p>
              </div>
              <Badge tone="neutral">{t("accounting.supplier.count", { count: data.movements.length })}</Badge>
            </header>
            {data.movements.length === 0 ? (
              <EmptyState
                title={t("accounting.supplier.emptyMovements")}
                description={t("accounting.supplier.emptyMovementsHint")}
                icon={<BookOpen className="size-5" />}
              />
            ) : (
              <div className="table-scroll overflow-x-auto">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t("accounting.supplier.columns.number")}</th>
                      <th>{t("accounting.supplier.columns.date")}</th>
                      <th>{t("accounting.supplier.columns.source")}</th>
                      <th>{t("accounting.supplier.columns.description")}</th>
                      <th className="text-end">{t("accounting.supplier.columns.debit")}</th>
                      <th className="text-end">{t("accounting.supplier.columns.credit")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.movements.map((row) => (
                      <tr key={`${row.entry_id}-${row.number}-${row.debit}-${row.credit}`}>
                        <td className="tabular text-ink">{row.number}</td>
                        <td className="tabular whitespace-nowrap text-ink-muted">{date(row.entry_date, lang)}</td>
                        <td className="text-ink-muted">
                          {lang === "ar" ? row.source_label_ar : row.source_label}
                        </td>
                        <td className="text-ink">{row.description}</td>
                        <td className="tabular text-end">{money(row.debit, currency, lang)}</td>
                        <td className="tabular text-end">{money(row.credit, currency, lang)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td colSpan={4} className="text-ink-muted">
                        {t("accounting.supplier.totals")}
                      </td>
                      <td className="tabular text-end font-medium text-ink">{money(data.debit_total, currency, lang)}</td>
                      <td className="tabular text-end font-medium text-ink">{money(data.credit_total, currency, lang)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </section>

          <section className="rounded border border-line bg-surface">
            <header className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
              <div>
                <h3 className="text-sm font-semibold text-ink">{t("accounting.supplier.ledger")}</h3>
                <p className="text-2xs text-ink-subtle">{t("accounting.supplier.ledgerHint")}</p>
              </div>
              <Badge tone="neutral">{t("accounting.supplier.count", { count: data.ledger_entries.length })}</Badge>
            </header>
            {data.ledger_entries.length === 0 ? (
              <EmptyState
                title={t("accounting.supplier.emptyLedger")}
                description={t("accounting.supplier.emptyLedgerHint")}
                icon={<Receipt className="size-5" />}
              />
            ) : (
              <div className="table-scroll overflow-x-auto">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t("accounting.supplier.columns.date")}</th>
                      <th>{t("accounting.supplier.columns.kind")}</th>
                      <th className="text-end">{t("accounting.supplier.columns.amount")}</th>
                      <th>{t("accounting.supplier.columns.method")}</th>
                      <th>{t("accounting.supplier.columns.reference")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.ledger_entries.map((row) => (
                      <tr key={row.id}>
                        <td className="tabular whitespace-nowrap text-ink-muted">{date(row.entry_date, lang)}</td>
                        <td>
                          <StatusChip status={row.kind} label={row.kind.replace(/_/g, " ")} />
                        </td>
                        <td className="tabular text-end text-ink">{money(row.amount, row.currency || currency, lang)}</td>
                        <td className="text-ink-muted">{row.method || "—"}</td>
                        <td className="text-ink-subtle">{row.reference || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>
      )}
    </Drawer>
  );
}
