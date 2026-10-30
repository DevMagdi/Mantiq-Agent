import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, CheckCircle2, FileSpreadsheet, Landmark, Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
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
  useToast,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, money } from "@/lib/format";
import { PERM } from "@/lib/permissions";
import { useAuth } from "@/lib/session";
import type { AppLang } from "@/i18n";
import type { CostCenter, PoAccounting, PurchaseOrderDetail } from "@/lib/types";

type Target = { kind: "accrual" } | { kind: "invoice"; invoiceId: string; number: string };

/**
 * The accounting hand-off for one purchase order: the journal entries the books already hold, the
 * cost they landed on, and the two human acts that create and post them.
 *
 * Nothing is written on render: drafting needs `accounting:write`, posting needs `accounting:post`,
 * and an invoice posting is refused by the API until the 3-way match is clean and an auditor has
 * accepted the document package.
 */
export function PostingsCard({
  po,
  onChanged,
}: {
  po: PurchaseOrderDetail;
  onChanged?: () => Promise<void> | void;
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<Target | null>(null);
  const [centerCode, setCenterCode] = useState("CC-000");
  const [unitNumber, setUnitNumber] = useState("");
  const [entryDate] = useState(() => new Date().toISOString().slice(0, 10));

  const readable = can(PERM.accountingRead);
  const query = useQuery({
    queryKey: ["po-accounting", po.id],
    queryFn: () => api.get<PoAccounting>(`/api/v1/purchase-orders/${po.id}/accounting`),
    enabled: readable,
  });
  const centers = useQuery({
    queryKey: ["cost-centers"],
    queryFn: () => api.get<{ items: CostCenter[] }>("/api/v1/accounting/cost-centers"),
    enabled: readable && can(PERM.costRead),
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["po-accounting", po.id] });
    await queryClient.invalidateQueries({ queryKey: ["journal-entries"] });
    await queryClient.invalidateQueries({ queryKey: ["trial-balance"] });
    await queryClient.invalidateQueries({ queryKey: ["cost-report"] });
    await queryClient.invalidateQueries({ queryKey: ["audit-package", po.id] });
    await onChanged?.();
  };

  const allocations = () =>
    po.lines.map((line) => ({
      line_no: line.line_no,
      cost_center_code: centerCode,
      unit_number: unitNumber || null,
    }));

  const draft = useMutation({
    mutationFn: (chosen: Target) => {
      const body = { allocations: allocations(), entry_date: `${entryDate}T00:00:00Z` };
      return chosen.kind === "accrual"
        ? api.post(`/api/v1/purchase-orders/${po.id}/accounting/accrual`, body)
        : api.post(`/api/v1/invoices/${chosen.invoiceId}/posting`, body);
    },
    onSuccess: async () => {
      toast.push(t("accounting.postings.toast.drafted"), "ok");
      setTarget(null);
      await refresh();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const post = useMutation({
    mutationFn: (entryId: string) => api.post(`/api/v1/accounting/journal-entries/${entryId}/post`),
    onSuccess: async () => {
      toast.push(t("accounting.toast.posted"), "ok");
      await refresh();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  if (!readable) return null;

  const data = query.data;
  const centerOptions: Pick<CostCenter, "code" | "name" | "name_ar">[] = centers.data?.items?.length
    ? centers.data.items
    : [{ code: "CC-000", name: "Unallocated", name_ar: "غير موزع" }];
  const mayApproveInvoices = po.invoices.filter((invoice) => invoice.status === "approved");
  const firstPayable = mayApproveInvoices[0];
  const hasAccrual = (data?.entries ?? []).some((entry) => entry.source_type === "receipt_accrual");
  const postedInvoice = (data?.entries ?? []).some((entry) => entry.source_type === "invoice");

  return (
    <Card>
      <CardHeader
        title={t("accounting.postings.title")}
        subtitle={t("accounting.postings.subtitle")}
        action={
          <div className="flex items-center gap-1.5">
            {data ? <Badge tone="neutral">{t("accounting.postings.count", { count: data.entries.length })}</Badge> : null}
            {can(PERM.accountingWrite) && !hasAccrual && po.receipts.some((row) => row.status === "confirmed") ? (
              <button type="button" className="btn-secondary btn-sm" onClick={() => setTarget({ kind: "accrual" })}>
                <Plus className="size-3.5" aria-hidden />
                {t("accounting.postings.draftAccrual")}
              </button>
            ) : null}
            {can(PERM.accountingWrite) && !postedInvoice && firstPayable ? (
              <button
                type="button"
                className="btn-secondary btn-sm"
                onClick={() =>
                  setTarget({
                    kind: "invoice",
                    invoiceId: firstPayable.id,
                    number: firstPayable.number,
                  })
                }
              >
                <FileSpreadsheet className="size-3.5" aria-hidden />
                {t("accounting.postings.draftInvoice", { number: firstPayable.number })}
              </button>
            ) : null}
          </div>
        }
      />
      {query.isLoading ? (
        <LoadingBlock rows={3} />
      ) : query.isError ? (
        <ErrorState message={errorMessage(query.error)} onRetry={() => void query.refetch()} />
      ) : (data?.entries ?? []).length === 0 ? (
        <EmptyState title={t("accounting.postings.empty")} description={t("accounting.postings.emptyHint")}
                    icon={<BookOpen className="size-5" />} />
      ) : (
        <ul className="divide-y divide-line/70">
          {(data?.entries ?? []).map((entry) => (
            <li key={entry.id} className="px-4 py-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-[0.8125rem] font-semibold text-ink">
                    {entry.number}
                    <span className="ms-2 text-2xs font-normal text-ink-subtle">
                      {lang === "ar" ? entry.source_label_ar : entry.source_label}
                    </span>
                  </p>
                  <p className="truncate text-2xs text-ink-subtle">
                    {date(entry.entry_date, lang)} · {money(entry.total_debit, entry.currency, lang)} ·{" "}
                    {entry.lines.length} {t("accounting.journal.linesShort")}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <StatusChip status={entry.status} label={lang === "ar" ? entry.status_label_ar : entry.status_label} />
                  {entry.status === "draft" && can(PERM.accountingPost) ? (
                    <button type="button" className="btn-primary btn-sm"
                            disabled={post.isPending && post.variables === entry.id}
                            onClick={() => post.mutate(entry.id)}>
                      {post.isPending && post.variables === entry.id ? <Spinner /> : <CheckCircle2 className="size-3.5" aria-hidden />}
                      {t("accounting.journal.post")}
                    </button>
                  ) : null}
                </div>
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[0.625rem] text-ink-subtle">
                {entry.lines.map((line) => (
                  <span key={line.line_no} className="tabular">
                    {line.account_code}
                    {line.cost_center_code ? ` · ${line.cost_center_code}` : ""}
                    {line.unit_number ? ` · ${line.unit_number}` : ""}
                    {" · "}
                    {Number(line.debit) > 0 ? `Dr ${money(line.debit, entry.currency, lang)}` : `Cr ${money(line.credit, entry.currency, lang)}`}
                  </span>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}

      {data && data.cost_by_center.items.length > 0 ? (
        <div className="border-t border-line px-4 py-3">
          <p className="eyebrow mb-2">
            <Landmark className="me-1 inline size-3" aria-hidden />
            {t("accounting.postings.costByCenter")}
          </p>
          <ul className="space-y-1">
            {data.cost_by_center.items.map((row) => (
              <li key={row.key || "unassigned"} className="flex items-center justify-between gap-2 text-2xs">
                <span className="truncate text-ink-muted">{lang === "ar" && row.label_ar ? row.label_ar : row.label}</span>
                <span className="tabular text-ink">{money(row.amount, data.currency, lang)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <Drawer
        open={Boolean(target)}
        onClose={() => setTarget(null)}
        title={target?.kind === "invoice" ? t("accounting.postings.draftInvoiceTitle") : t("accounting.postings.draftAccrualTitle")}
        description={t("accounting.postings.allocHint")}
        footer={
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-ghost btn-sm" onClick={() => setTarget(null)}>
              {t("common.cancel")}
            </button>
            <button type="button" className="btn-primary btn-sm" disabled={draft.isPending}
                    onClick={() => target && draft.mutate(target)}>
              {draft.isPending ? <Spinner /> : <Plus className="size-3.5" aria-hidden />}
              {t("accounting.postings.draft")}
            </button>
          </div>
        }
      >
        <div className="space-y-3">
          <label className="block">
            <span className="eyebrow">{t("accounting.journal.columns.costCenter")}</span>
            <select className="input mt-1 w-full" value={centerCode} onChange={(event) => setCenterCode(event.target.value)}>
              {centerOptions.map((center) => (
                <option key={center.code} value={center.code}>
                  {center.code} · {lang === "ar" && center.name_ar ? center.name_ar : center.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="eyebrow">{t("accounting.journal.columns.unit")}</span>
            <input className="input mt-1 w-full" value={unitNumber}
                   onChange={(event) => setUnitNumber(event.target.value)}
                   placeholder={t("accounting.postings.unitPlaceholder")} />
          </label>
          <p className="text-2xs text-ink-subtle">{t("accounting.postings.sodHint")}</p>
        </div>
      </Drawer>
    </Card>
  );
}
