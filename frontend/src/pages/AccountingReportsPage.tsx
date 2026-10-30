import { useQuery } from "@tanstack/react-query";
import { BarChart3, Landmark, Scale, Wallet } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeading } from "@/components/Shell";
import { SupplierAccountDrawer } from "@/components/SupplierAccountDrawer";
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  Kpi,
  LoadingBlock,
  Progress,
  StatusChip,
  Tabs,
  TabList,
  TabPanel,
  cn,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, money, num } from "@/lib/format";
import { PERM } from "@/lib/permissions";
import { useAuth } from "@/lib/session";
import type { AppLang } from "@/i18n";
import type { CostReport, TrialBalance } from "@/lib/types";

type GroupBy = CostReport["group_by"];

type BalanceRow = {
  supplier_id: string;
  supplier_name: string;
  supplier_name_ar: string;
  outstanding: string;
  overdue: string;
  invoiced_total: string;
  paid_total: string;
  currency: string;
  open_invoices: number;
};

export default function AccountingReportsPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const { can } = useAuth();
  const [groupBy, setGroupBy] = useState<GroupBy>("cost_center");
  const [tab, setTab] = useState("trial");
  const [ledger, setLedger] = useState<{ id: string; name: string } | null>(null);

  const trial = useQuery({
    queryKey: ["trial-balance"],
    queryFn: () => api.get<TrialBalance>("/api/v1/accounting/trial-balance"),
  });
  const cost = useQuery({
    queryKey: ["cost-report", groupBy],
    queryFn: () => api.get<CostReport>(`/api/v1/accounting/cost-report?group_by=${groupBy}`),
  });
  const balances = useQuery({
    queryKey: ["supplier-balances"],
    queryFn: () => api.get<{ items: BalanceRow[]; total_outstanding: string; total_overdue: string; currency: string }>(
      "/api/v1/supplier-balances",
    ),
    enabled: can(PERM.ledgerRead),
  });

  const groups: { value: GroupBy; label: string }[] = [
    { value: "cost_center", label: t("accounting.reports.group.cost_center") },
    { value: "item", label: t("accounting.reports.group.item") },
    { value: "unit", label: t("accounting.reports.group.unit") },
    { value: "supplier", label: t("accounting.reports.group.supplier") },
    { value: "po", label: t("accounting.reports.group.po") },
  ];

  return (
    <div className="space-y-4">
      <PageHeading eyebrow={t("accounting.eyebrow")} title={t("accounting.reports.title")}
                   subtitle={t("accounting.reports.subtitle")} />

      <div className="grid gap-3 sm:grid-cols-3">
        <Kpi label={t("accounting.reports.kpi.debits")} icon={<Scale className="size-3.5" />}
             value={money(trial.data?.total_debit ?? "0", trial.data?.currency || "EGP", lang)}
             hint={t("accounting.reports.kpi.debitsHint")} />
        <Kpi label={t("accounting.reports.kpi.credits")} icon={<Landmark className="size-3.5" />}
             value={money(trial.data?.total_credit ?? "0", trial.data?.currency || "EGP", lang)}
             hint={t("accounting.reports.kpi.creditsHint")} />
        <Kpi label={t("accounting.reports.kpi.balance")} icon={<BarChart3 className="size-3.5" />}
             value={trial.data?.balanced ? t("accounting.reports.kpi.balanced") : t("accounting.reports.kpi.unbalanced")}
             hint={t("accounting.reports.kpi.balanceHint")} />
      </div>

      <Card>
        <Tabs value={tab} onValueChange={setTab}>
          <div className="card-header">
            <TabList
              items={[
                { value: "trial", label: t("accounting.reports.tabs.trial") },
                { value: "cost", label: t("accounting.reports.tabs.cost") },
                ...(can(PERM.ledgerRead) ? [{ value: "balances", label: t("accounting.reports.tabs.balances") }] : []),
              ]}
            />
          </div>

          <TabPanel value="trial">
            {trial.isLoading ? (
              <LoadingBlock />
            ) : trial.isError ? (
              <ErrorState message={errorMessage(trial.error)} onRetry={() => void trial.refetch()} />
            ) : (trial.data?.items ?? []).length === 0 ? (
              <EmptyState title={t("accounting.reports.trialEmpty")} description={t("accounting.reports.trialEmptyHint")}
                          icon={<Scale className="size-5" />} />
            ) : (
              <div className="table-scroll overflow-x-auto">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t("accounting.reports.columns.code")}</th>
                      <th>{t("accounting.reports.columns.account")}</th>
                      <th>{t("accounting.reports.columns.kind")}</th>
                      <th className="text-end">{t("accounting.journal.columns.debit")}</th>
                      <th className="text-end">{t("accounting.journal.columns.credit")}</th>
                      <th className="text-end">{t("accounting.reports.columns.balance")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(trial.data?.items ?? []).map((row) => (
                      <tr key={row.account_code}>
                        <td className="tabular font-medium text-ink">{row.code}</td>
                        <td className="text-ink">{lang === "ar" && row.account_name_ar ? row.account_name_ar : row.account_name}</td>
                        <td className="text-ink-muted">{t(`accounting.kind.${row.kind}`)}</td>
                        <td className="tabular text-end">{money(row.debit, trial.data?.currency || "EGP", lang)}</td>
                        <td className="tabular text-end">{money(row.credit, trial.data?.currency || "EGP", lang)}</td>
                        <td className={cn("tabular text-end font-medium", Number(row.balance) === 0 ? "text-ink-muted" : "text-ink")}>
                          {money(row.balance, trial.data?.currency || "EGP", lang)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="border-t border-line">
                      <td colSpan={3} className="text-end text-2xs uppercase tracking-wide text-ink-subtle">
                        {t("common.total")}
                      </td>
                      <td className="tabular text-end font-semibold text-ink">
                        {money(trial.data?.total_debit ?? "0", trial.data?.currency || "EGP", lang)}
                      </td>
                      <td className="tabular text-end font-semibold text-ink">
                        {money(trial.data?.total_credit ?? "0", trial.data?.currency || "EGP", lang)}
                      </td>
                      <td />
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </TabPanel>

          <TabPanel value="cost">
            <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2">
              {groups.map((group) => (
                <button
                  key={group.value}
                  type="button"
                  className={cn("chip", groupBy === group.value ? "border-gold/60 bg-gold/10 text-[color:var(--gold-300)]" : "border-line text-ink-muted")}
                  onClick={() => setGroupBy(group.value)}
                  aria-pressed={groupBy === group.value}
                >
                  {group.label}
                </button>
              ))}
            </div>
            {cost.isLoading ? (
              <LoadingBlock />
            ) : cost.isError ? (
              <ErrorState message={errorMessage(cost.error)} onRetry={() => void cost.refetch()} />
            ) : (cost.data?.items ?? []).length === 0 ? (
              <EmptyState title={t("accounting.reports.costEmpty")} description={t("accounting.reports.costEmptyHint")}
                          icon={<BarChart3 className="size-5" />} />
            ) : (
              <div className="overflow-x-auto">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t(`accounting.reports.group.${groupBy}`)}</th>
                      <th className="text-end">{t("accounting.reports.columns.amount")}</th>
                      <th className="w-40">{t("accounting.reports.columns.share")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(cost.data?.items ?? []).map((row) => (
                      <tr key={row.key || "unassigned"}>
                        <td>
                          <span className="block truncate text-ink">
                            {lang === "ar" && row.label_ar ? row.label_ar : row.label}
                          </span>
                          <span className="tabular text-2xs text-ink-subtle">{row.key || "—"}</span>
                        </td>
                        <td className="tabular text-end">{money(row.amount, cost.data?.currency || "EGP", lang)}</td>
                        <td>
                          <div className="flex items-center gap-2">
                            <Progress value={row.share_pct} />
                            <span className="tabular w-12 text-end text-2xs text-ink-muted">{row.share_pct.toFixed(1)}%</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="border-t border-line">
                      <td className="text-end text-2xs uppercase tracking-wide text-ink-subtle">{t("common.total")}</td>
                      <td className="tabular text-end font-semibold text-ink">
                        {money(cost.data?.total ?? "0", cost.data?.currency || "EGP", lang)}
                      </td>
                      <td />
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </TabPanel>

          {can(PERM.ledgerRead) ? (
            <TabPanel value="balances">
              {balances.isLoading ? (
                <LoadingBlock />
              ) : balances.isError ? (
                <ErrorState message={errorMessage(balances.error)} onRetry={() => void balances.refetch()} />
              ) : (balances.data?.items ?? []).length === 0 ? (
                <EmptyState title={t("accounting.reports.balancesEmpty")}
                            description={t("accounting.reports.balancesEmptyHint")} icon={<Wallet className="size-5" />} />
              ) : (
                <div className="table-scroll overflow-x-auto">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t("accounting.reports.columns.supplier")}</th>
                        <th className="text-end">{t("accounting.reports.columns.invoiced")}</th>
                        <th className="text-end">{t("accounting.reports.columns.paid")}</th>
                        <th className="text-end">{t("accounting.reports.columns.outstanding")}</th>
                        <th className="text-end">{t("accounting.reports.columns.overdue")}</th>
                        <th className="text-end">{t("accounting.reports.columns.openInvoices")}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {(balances.data?.items ?? []).map((row) => (
                        <tr key={row.supplier_id}>
                          <td className="text-ink">
                            {lang === "ar" && row.supplier_name_ar ? row.supplier_name_ar : row.supplier_name}
                          </td>
                          <td className="tabular text-end">{money(row.invoiced_total, row.currency, lang)}</td>
                          <td className="tabular text-end">{money(row.paid_total, row.currency, lang)}</td>
                          <td className="tabular text-end font-medium text-ink">{money(row.outstanding, row.currency, lang)}</td>
                          <td className="tabular text-end">
                            {Number(row.overdue) > 0 ? (
                              <span className="text-[color:var(--danger-ink)]">{money(row.overdue, row.currency, lang)}</span>
                            ) : (
                              <span className="text-ink-subtle">—</span>
                            )}
                          </td>
                          <td className="tabular text-end text-ink-muted">{num(row.open_invoices, lang)}</td>
                          <td className="text-end">
                            <button
                              type="button"
                              className="btn-ghost btn-sm"
                              onClick={() =>
                                setLedger({
                                  id: row.supplier_id,
                                  name: lang === "ar" && row.supplier_name_ar ? row.supplier_name_ar : row.supplier_name,
                                })
                              }
                            >
                              {t("accounting.supplier.openLedger")}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </TabPanel>
          ) : null}
        </Tabs>
      </Card>

      <Card>
        <CardHeader title={t("accounting.reports.sodTitle")} subtitle={t("accounting.reports.sodBody")} />
        <div className="flex flex-wrap gap-2 p-4">
          <StatusChip status="draft" label={t("accounting.journal.filters.draft")} />
          <StatusChip status="posted" label={t("accounting.journal.filters.posted")} />
          <StatusChip status="reversed" label={t("accounting.journal.filters.reversed")} />
          <span className="text-2xs text-ink-subtle">{date(new Date().toISOString(), lang)}</span>
        </div>
      </Card>

      <SupplierAccountDrawer
        supplierId={ledger?.id ?? null}
        supplierName={ledger?.name}
        onClose={() => setLedger(null)}
      />
    </div>
  );
}
