import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, CheckCircle2, FileText, Plus, RotateCcw, Scale, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeading } from "@/components/Shell";
import {
  Badge,
  Card,
  Drawer,
  EmptyState,
  ErrorState,
  Kpi,
  LoadingBlock,
  Spinner,
  StatusChip,
  Tabs,
  TabList,
  TabPanel,
  cn,
  useToast,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, money, num } from "@/lib/format";
import { useAuth } from "@/lib/session";
import { PERM } from "@/lib/permissions";
import type { AppLang } from "@/i18n";
import type { CostCenter, GlAccount, JournalEntryView, JournalList } from "@/lib/types";

type DraftLine = {
  account_code: string;
  description: string;
  debit: string;
  credit: string;
  cost_center_code: string;
  unit_number: string;
};

const emptyLine = (): DraftLine => ({
  account_code: "",
  description: "",
  debit: "",
  credit: "",
  cost_center_code: "",
  unit_number: "",
});

export default function JournalPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [status, setStatus] = useState<"all" | "draft" | "posted" | "reversed">("all");
  const [selected, setSelected] = useState<JournalEntryView | null>(null);
  const [reverseReason, setReverseReason] = useState("");
  const [composeOpen, setComposeOpen] = useState(false);
  const [memo, setMemo] = useState("");
  const [memoAr, setMemoAr] = useState("");
  const [draftLines, setDraftLines] = useState<DraftLine[]>([emptyLine(), emptyLine()]);

  const entries = useQuery({
    queryKey: ["journal-entries", status],
    queryFn: () =>
      api.get<JournalList>(`/api/v1/accounting/journal-entries${status === "all" ? "" : `?status=${status}`}`),
  });
  const accounts = useQuery({
    queryKey: ["gl-accounts"],
    queryFn: () => api.get<{ items: GlAccount[] }>("/api/v1/accounting/accounts"),
  });
  const centers = useQuery({
    queryKey: ["cost-centers"],
    queryFn: () => api.get<{ items: CostCenter[] }>("/api/v1/accounting/cost-centers"),
  });

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["journal-entries"] });
    await queryClient.invalidateQueries({ queryKey: ["trial-balance"] });
    await queryClient.invalidateQueries({ queryKey: ["cost-report"] });
  };

  const post = useMutation({
    mutationFn: (id: string) => api.post<JournalEntryView>(`/api/v1/accounting/journal-entries/${id}/post`),
    onSuccess: async () => {
      toast.push(t("accounting.toast.posted"), "ok");
      await invalidate();
      setSelected(null);
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const reverse = useMutation({
    mutationFn: (id: string) =>
      api.post<JournalEntryView>(`/api/v1/accounting/journal-entries/${id}/reverse`, { reason: reverseReason }),
    onSuccess: async () => {
      toast.push(t("accounting.toast.reversed"), "ok");
      setReverseReason("");
      await invalidate();
      setSelected(null);
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<JournalEntryView>("/api/v1/accounting/journal-entries", {
        memo: memo || undefined,
        memo_ar: memoAr || undefined,
        lines: draftLines
          .filter((line) => line.account_code && (Number(line.debit) > 0 || Number(line.credit) > 0))
          .map((line, index) => ({
            line_no: index + 1,
            account_code: line.account_code,
            description: line.description,
            debit: line.debit || "0",
            credit: line.credit || "0",
            cost_center_code: line.cost_center_code || null,
            unit_number: line.unit_number || null,
          })),
      }),
    onSuccess: async () => {
      toast.push(t("accounting.toast.drafted"), "ok");
      setComposeOpen(false);
      setMemo("");
      setMemoAr("");
      setDraftLines([emptyLine(), emptyLine()]);
      await invalidate();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const totals = useMemo(() => {
    const debit = draftLines.reduce((sum, line) => sum + (Number(line.debit) || 0), 0);
    const credit = draftLines.reduce((sum, line) => sum + (Number(line.credit) || 0), 0);
    return { debit, credit, balanced: Math.abs(debit - credit) < 0.01 && debit > 0 };
  }, [draftLines]);

  const rows = entries.data?.items ?? [];

  return (
    <div className="space-y-4">
      <PageHeading
        eyebrow={t("accounting.eyebrow")}
        title={t("accounting.journal.title")}
        subtitle={t("accounting.journal.subtitle")}
        actions={
          can(PERM.accountingWrite) ? (
            <button type="button" className="btn-primary btn-sm" onClick={() => setComposeOpen(true)}>
              <Plus className="size-3.5" aria-hidden />
              {t("accounting.journal.new")}
            </button>
          ) : null
        }
      />

      <div className="grid gap-3 sm:grid-cols-3">
        <Kpi label={t("accounting.journal.kpi.entries")} value={num(entries.data?.total ?? 0, lang)}
             icon={<BookOpen className="size-3.5" />} hint={t("accounting.journal.kpi.entriesHint")} />
        <Kpi label={t("accounting.journal.kpi.posted")} value={num(entries.data?.posted ?? 0, lang)}
             icon={<CheckCircle2 className="size-3.5" />} hint={t("accounting.journal.kpi.postedHint")} />
        <Kpi label={t("accounting.journal.kpi.value")} value={money(entries.data?.posted_total ?? "0", "EGP", lang)}
             icon={<Scale className="size-3.5" />} hint={t("accounting.journal.kpi.valueHint")} />
      </div>

      <Card>
        <Tabs value={status} onValueChange={(value) => setStatus(value as typeof status)}>
          <div className="card-header">
            <TabList
              items={[
                { value: "all", label: t("accounting.journal.filters.all") },
                { value: "draft", label: t("accounting.journal.filters.draft") },
                { value: "posted", label: t("accounting.journal.filters.posted") },
                { value: "reversed", label: t("accounting.journal.filters.reversed") },
              ]}
            />
          </div>
          {(["all", "draft", "posted", "reversed"] as const).map((value) => (
            <TabPanel key={value} value={value}>
              {entries.isLoading ? (
                <LoadingBlock />
              ) : entries.isError ? (
                <ErrorState message={errorMessage(entries.error)} onRetry={() => void entries.refetch()} />
              ) : rows.length === 0 ? (
                <EmptyState title={t("accounting.journal.empty")} description={t("accounting.journal.emptyHint")}
                            icon={<FileText className="size-5" />} />
              ) : (
                <div className="table-scroll overflow-x-auto">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>{t("accounting.journal.columns.number")}</th>
                        <th>{t("accounting.journal.columns.date")}</th>
                        <th>{t("accounting.journal.columns.memo")}</th>
                        <th>{t("accounting.journal.columns.source")}</th>
                        <th className="text-end">{t("accounting.journal.columns.debit")}</th>
                        <th className="text-end">{t("accounting.journal.columns.credit")}</th>
                        <th>{t("accounting.journal.columns.status")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((entry) => (
                        <tr
                          key={entry.id}
                          className="cursor-pointer"
                          onClick={() => {
                            setSelected(entry);
                            setReverseReason("");
                          }}
                        >
                          <td className="tabular font-medium text-ink">{entry.number}</td>
                          <td className="tabular text-ink-muted">{date(entry.entry_date, lang)}</td>
                          <td className="max-w-[20rem]">
                            <span className="block truncate text-ink">{lang === "ar" && entry.memo_ar ? entry.memo_ar : entry.memo}</span>
                            <span className="text-2xs text-ink-subtle">{entry.lines.length} {t("accounting.journal.linesShort")}</span>
                          </td>
                          <td className="text-ink-muted">
                            {lang === "ar" ? entry.source_label_ar : entry.source_label}
                          </td>
                          <td className="tabular text-end">{money(entry.total_debit, entry.currency, lang)}</td>
                          <td className="tabular text-end">{money(entry.total_credit, entry.currency, lang)}</td>
                          <td>
                            <StatusChip status={entry.status}
                                        label={lang === "ar" ? entry.status_label_ar : entry.status_label} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </TabPanel>
          ))}
        </Tabs>
      </Card>

      {/* ---------------------------------------------------------------- entry detail */}
      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected ? `${selected.number} · ${selected.status_label}` : ""}
        description={selected ? (lang === "ar" && selected.memo_ar ? selected.memo_ar : selected.memo) : undefined}
        width="max-w-2xl"
        footer={
          selected ? (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-2xs text-ink-subtle">
                {selected.balanced ? t("accounting.journal.balanced") : t("accounting.journal.unbalanced")}
              </span>
              <div className="flex flex-wrap gap-2">
                {selected.status === "draft" && can(PERM.accountingPost) ? (
                  <button type="button" className="btn-primary btn-sm" disabled={post.isPending}
                          onClick={() => post.mutate(selected.id)}>
                    {post.isPending ? <Spinner /> : <CheckCircle2 className="size-3.5" aria-hidden />}
                    {t("accounting.journal.post")}
                  </button>
                ) : null}
                {selected.status === "posted" && can(PERM.accountingReverse) ? (
                  <button type="button" className="btn-secondary btn-sm" disabled={reverse.isPending || !reverseReason.trim()}
                          onClick={() => reverse.mutate(selected.id)}>
                    {reverse.isPending ? <Spinner /> : <RotateCcw className="size-3.5" aria-hidden />}
                    {t("accounting.journal.reverse")}
                  </button>
                ) : null}
              </div>
            </div>
          ) : null
        }
      >
        {selected ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-2xs sm:grid-cols-4">
              <Figure label={t("accounting.journal.columns.date")} value={date(selected.entry_date, lang)} />
              <Figure label={t("accounting.journal.columns.source")}
                      value={lang === "ar" ? selected.source_label_ar : selected.source_label} />
              <Figure label={t("accounting.journal.createdBy")} value={selected.created_by ?? "—"} />
              <Figure label={t("accounting.journal.postedBy")}
                      value={selected.posted_at ? `${selected.posted_by ?? ""}` : t("accounting.journal.notPosted")} />
            </div>

            <div className="rounded-md border border-line">
              <table className="table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>{t("accounting.journal.columns.account")}</th>
                    <th>{t("accounting.journal.columns.description")}</th>
                    <th>{t("accounting.journal.columns.costCenter")}</th>
                    <th>{t("accounting.journal.columns.unit")}</th>
                    <th className="text-end">{t("accounting.journal.columns.debit")}</th>
                    <th className="text-end">{t("accounting.journal.columns.credit")}</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.lines.map((line) => (
                    <tr key={line.line_no}>
                      <td className="tabular text-ink-subtle">{line.line_no}</td>
                      <td>
                        <span className="tabular text-ink">{line.account_code}</span>
                        <span className="block text-2xs text-ink-subtle">{line.account_name ?? ""}</span>
                      </td>
                      <td className="max-w-[16rem] truncate text-ink-muted">{line.description}</td>
                      <td className="tabular text-ink-muted">{line.cost_center_code ?? "—"}</td>
                      <td className="tabular text-ink-muted">{line.unit_number ?? "—"}</td>
                      <td className="tabular text-end">{Number(line.debit) ? money(line.debit, selected.currency, lang) : "—"}</td>
                      <td className="tabular text-end">{Number(line.credit) ? money(line.credit, selected.currency, lang) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-line">
                    <td colSpan={5} className="text-end text-2xs uppercase tracking-wide text-ink-subtle">
                      {t("common.total")}
                    </td>
                    <td className="tabular text-end font-semibold text-ink">{money(selected.total_debit, selected.currency, lang)}</td>
                    <td className="tabular text-end font-semibold text-ink">{money(selected.total_credit, selected.currency, lang)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>

            {selected.reversal_of || selected.reversed_by ? (
              <div className="rounded-md border border-line bg-surface-raised/40 px-3 py-2 text-2xs text-ink-muted">
                {selected.reversal_of ? <p>{t("accounting.journal.isReversal", { id: selected.reversal_of })}</p> : null}
                {selected.reversed_by ? <p>{t("accounting.journal.wasReversed")}</p> : null}
                {selected.reversal_reason ? <p className="mt-1 text-ink">{selected.reversal_reason}</p> : null}
              </div>
            ) : null}

            {selected.status === "posted" && can(PERM.accountingReverse) ? (
              <label className="block">
                <span className="eyebrow">{t("accounting.journal.reverseReason")}</span>
                <textarea
                  value={reverseReason}
                  onChange={(event) => setReverseReason(event.target.value)}
                  rows={2}
                  className="input mt-1 w-full"
                  placeholder={t("accounting.journal.reverseReasonHint")}
                />
              </label>
            ) : null}

            <p className="text-2xs text-ink-subtle">{t("accounting.journal.appendOnly")}</p>
          </div>
        ) : null}
      </Drawer>

      {/* ---------------------------------------------------------------- compose */}
      <Drawer
        open={composeOpen}
        onClose={() => setComposeOpen(false)}
        title={t("accounting.journal.new")}
        description={t("accounting.journal.newHint")}
        width="max-w-3xl"
        footer={
          <div className="flex items-center justify-between gap-2">
            <Badge tone={totals.balanced ? "ok" : "warn"}>
              {t("accounting.journal.balanceLine", {
                debit: num(totals.debit, lang, 2),
                credit: num(totals.credit, lang, 2),
              })}
            </Badge>
            <button type="button" className="btn-primary btn-sm"
                    disabled={!totals.balanced || create.isPending}
                    onClick={() => create.mutate()}>
              {create.isPending ? <Spinner /> : <Plus className="size-3.5" aria-hidden />}
              {t("accounting.journal.draftEntry")}
            </button>
          </div>
        }
      >
        <div className="space-y-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block">
              <span className="eyebrow">{t("accounting.journal.columns.memo")}</span>
              <input className="input mt-1 w-full" value={memo} onChange={(event) => setMemo(event.target.value)}
                     placeholder={t("accounting.journal.memoPlaceholder")} />
            </label>
            <label className="block">
              <span className="eyebrow">{t("accounting.journal.memoAr")}</span>
              <input className="input mt-1 w-full" dir="rtl" value={memoAr}
                     onChange={(event) => setMemoAr(event.target.value)} />
            </label>
          </div>

          {accounts.data ? (
            <p className="text-2xs text-ink-subtle">
              {t("accounting.journal.accountHint", { count: accounts.data.items.length })}
            </p>
          ) : null}

          <div className="space-y-2">
            {draftLines.map((line, index) => (
              <div key={index} className="grid grid-cols-2 gap-2 rounded-md border border-line/70 p-2 sm:grid-cols-12">
                <label className="col-span-2 sm:col-span-3">
                  <span className="eyebrow">{t("accounting.journal.columns.account")}</span>
                  <select
                    className="input mt-1 w-full"
                    value={line.account_code}
                    onChange={(event) =>
                      setDraftLines((rows) =>
                        rows.map((row, rowIndex) =>
                          rowIndex === index ? { ...row, account_code: event.target.value } : row,
                        ),
                      )
                    }
                  >
                    <option value="">—</option>
                    {(accounts.data?.items ?? []).filter((account) => account.is_active).map((account) => (
                      <option key={account.code} value={account.code}>
                        {account.code} · {lang === "ar" && account.name_ar ? account.name_ar : account.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="col-span-2 sm:col-span-3">
                  <span className="eyebrow">{t("accounting.journal.columns.description")}</span>
                  <input className="input mt-1 w-full" value={line.description}
                         onChange={(event) =>
                           setDraftLines((rows) =>
                             rows.map((row, rowIndex) =>
                               rowIndex === index ? { ...row, description: event.target.value } : row,
                             ),
                           )
                         } />
                </label>
                <label className="sm:col-span-2">
                  <span className="eyebrow">{t("accounting.journal.columns.debit")}</span>
                  <input className="input mt-1 w-full tabular" inputMode="decimal" value={line.debit}
                         onChange={(event) =>
                           setDraftLines((rows) =>
                             rows.map((row, rowIndex) =>
                               rowIndex === index ? { ...row, debit: event.target.value, credit: "" } : row,
                             ),
                           )
                         } />
                </label>
                <label className="sm:col-span-2">
                  <span className="eyebrow">{t("accounting.journal.columns.credit")}</span>
                  <input className="input mt-1 w-full tabular" inputMode="decimal" value={line.credit}
                         onChange={(event) =>
                           setDraftLines((rows) =>
                             rows.map((row, rowIndex) =>
                               rowIndex === index ? { ...row, credit: event.target.value, debit: "" } : row,
                             ),
                           )
                         } />
                </label>
                <label className="sm:col-span-1">
                  <span className="eyebrow">{t("accounting.journal.columns.costCenter")}</span>
                  <select className="input mt-1 w-full" value={line.cost_center_code}
                          onChange={(event) =>
                            setDraftLines((rows) =>
                              rows.map((row, rowIndex) =>
                                rowIndex === index ? { ...row, cost_center_code: event.target.value } : row,
                              ),
                            )
                          }>
                    <option value="">—</option>
                    {(centers.data?.items ?? []).map((center) => (
                      <option key={center.code} value={center.code}>
                        {center.code}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="col-span-2 sm:col-span-1 flex items-end">
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    aria-label={t("accounting.journal.removeLine")}
                    disabled={draftLines.length <= 2}
                    onClick={() => setDraftLines((rows) => rows.filter((_, rowIndex) => rowIndex !== index))}
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                  </button>
                </div>
              </div>
            ))}
            <button type="button" className="btn-secondary btn-sm" onClick={() => setDraftLines((rows) => [...rows, emptyLine()])}>
              <Plus className="size-3.5" aria-hidden />
              {t("accounting.journal.addLine")}
            </button>
          </div>
          <p className="text-2xs text-ink-subtle">{t("accounting.journal.sodHint")}</p>
        </div>
      </Drawer>
    </div>
  );
}

function Figure({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className={cn("rounded-md border border-line/70 bg-surface-raised/40 px-2 py-1.5", className)}>
      <p className="text-[0.625rem] uppercase tracking-[0.1em] text-ink-subtle">{label}</p>
      <p className="mt-0.5 truncate text-2xs text-ink" title={value}>{value}</p>
    </div>
  );
}
