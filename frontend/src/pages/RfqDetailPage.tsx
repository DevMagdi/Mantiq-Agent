import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  Eye,
  FileSpreadsheet,
  Lock,
  Send,
  ShieldCheck,
  Sparkles,
  ThumbsUp,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { PageHeading } from "@/components/Shell";
import { RfqChannelPanel } from "@/components/RfqChannelPanel";
import {
  Badge,
  Card,
  CardHeader,
  Drawer,
  EmptyState,
  ErrorState,
  LoadingBlock,
  Progress,
  Spinner,
  StatusChip,
  TabList,
  TabPanel,
  Tabs,
  cn,
  useToast,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, money, num, percent, relative } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type {
  Comparison,
  ComparisonColumn,
  Recommendation,
  ExtractedField,
  Extraction,
  NegotiationDraft,
  NegotiationPolicy,
  Page,
  RFQDetail,
} from "@/lib/types";

export default function RfqDetailPage() {
  const { id = "" } = useParams();
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const [tab, setTab] = useState("overview");
  const [verifying, setVerifying] = useState<Extraction | null>(null);

  const rfq = useQuery({ queryKey: ["rfq", id], queryFn: () => api.get<RFQDetail>(`/api/v1/rfqs/${id}`) });
  const comparison = useQuery({ queryKey: ["comparison", id], queryFn: () => api.get<Comparison>(`/api/v1/rfqs/${id}/comparison`) });
  const extractions = useQuery({
    queryKey: ["extractions", id],
    queryFn: () => api.get<Page<Extraction>>(`/api/v1/extractions?rfq_id=${id}&page_size=50`),
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["rfq", id] }),
      queryClient.invalidateQueries({ queryKey: ["comparison", id] }),
      queryClient.invalidateQueries({ queryKey: ["extractions", id] }),
      queryClient.invalidateQueries({ queryKey: ["board"] }),
    ]);
  };

  const dispatch = useMutation({
    mutationFn: () => api.post(`/api/v1/rfqs/${id}/dispatch`),
    onSuccess: async () => {
      toast.push(t("rfqs.dispatch"), "ok");
      await refresh();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  if (rfq.isLoading) return <LoadingBlock rows={8} />;
  if (rfq.error || !rfq.data) return <ErrorState message={errorMessage(rfq.error)} onRetry={() => void rfq.refetch()} />;

  const data = rfq.data;
  const offers = comparison.data?.columns ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-xs text-ink-muted">
        <button type="button" className="inline-flex items-center gap-1 hover:text-ink" onClick={() => navigate("/rfqs")}>
          <ArrowLeft className="size-3 rtl:rotate-180" aria-hidden /> {t("rfqs.title")}
        </button>
      </div>
      <PageHeading
        title={`${data.code} · ${data.title}`}
        subtitle={`${data.pr?.code ?? ""} · ${t("rfqs.fields.mode")}: ${t(`rfqs.modes.${data.mode}`)}`}
        actions={
          <>
            <StatusChip status={data.status} />
            {data.status === "draft" ? (
              <button type="button" className="btn-primary" onClick={() => dispatch.mutate()} disabled={dispatch.isPending}>
                {dispatch.isPending ? <Spinner /> : <Send className="size-4" aria-hidden />} {t("rfqs.dispatch")}
              </button>
            ) : null}
            <button
              type="button"
              className="btn-secondary"
              disabled={offers.length === 0}
              onClick={() => {
                if (comparison.data) downloadComparison(comparison.data, `${data.code}-comparison.csv`);
              }}
              title={offers.length === 0 ? t("common.empty") : t("common.export")}
            >
              <Download className="size-4" aria-hidden /> {t("common.export")}
            </button>
          </>
        }
      />

      <Tabs value={tab} onValueChange={setTab}>
        <TabList
          items={[
            { value: "overview", label: t("common.details") },
            { value: "comparison", label: `${t("rfqs.comparison")} (${offers.length})` },
            { value: "negotiation", label: t("rfqs.negotiation") },
            { value: "extractions", label: t("agents.verification") },
            { value: "messages", label: t("rfqs.messages") },
          ]}
        />

        <TabPanel value="overview">
          <div className="grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader title={t("requests.lines")} />
              <div className="overflow-x-auto">
                <table className="table-dense">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>{t("requests.lines")}</th>
                      <th className="text-end">{t("common.quantity")}</th>
                      <th className="text-end">{t("rfqs.fields.targetPrice")}</th>
                      <th>{t("common.status")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.lines.map((line) => (
                      <tr key={line.id}>
                        <td className="text-xs text-ink-subtle">{line.line_no}</td>
                        <td>
                          <p className="text-sm">{line.description}</p>
                          {line.taxonomy_code ? <p className="font-mono text-2xs text-gold">{line.taxonomy_code}</p> : null}
                        </td>
                        <td className="text-end tabular text-sm">{num(line.quantity, lang, 2)} {line.uom}</td>
                        <td className="text-end tabular text-sm">{line.target_price ? money(line.target_price, data.currency, lang) : "—"}</td>
                        <td>{data.suppliers.length > 0 ? <Badge tone="ok">{`${data.suppliers.length}`}</Badge> : <Badge>{t("common.none")}</Badge>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>

            <Card>
              <CardHeader title={t("suppliers.title")} subtitle={`${data.suppliers.length}`} />
              <div className="divide-y divide-line/60">
                {data.suppliers.map((supplier) => (
                  <div key={supplier.invitation_id} className="flex items-center justify-between gap-2 px-4 py-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm">{lang === "ar" && supplier.name_ar ? supplier.name_ar : supplier.name}</p>
                      <p className="text-2xs text-ink-subtle">{supplier.channel} · {supplier.sent_at ? relative(supplier.sent_at, lang) : "—"}</p>
                    </div>
                    <StatusChip status={supplier.status} />
                  </div>
                ))}
                {data.suppliers.length === 0 ? <EmptyState title={t("common.empty")} /> : null}
              </div>
            </Card>
          </div>
          <div className="mt-4">
            <RfqChannelPanel rfqId={data.id} onSent={() => void refresh()} />
          </div>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader title={t("rfqs.timeline")} />
              <ol className="space-y-3 p-4">
                {(data.messages ?? []).slice(0, 6).map((message) => (
                  <li key={message.id} className="flex gap-3">
                    <span className={cn("mt-1 size-2 shrink-0 rounded-full", message.direction === "inbound" ? "bg-[color:var(--info)]" : "bg-gold")} />
                    <div className="min-w-0">
                      <p className="text-xs text-ink-muted">{message.channel} · {message.author_type} · {relative(message.created_at, lang)}</p>
                      <p className="line-clamp-2 text-sm text-ink">{message.body}</p>
                    </div>
                  </li>
                ))}
                {(data.messages ?? []).length === 0 ? <EmptyState title={t("common.empty")} /> : null}
              </ol>
            </Card>
            <Card>
              <CardHeader title={t("rfqs.fields.mode")} subtitle={t("rfqs.policyGuard")} />
              <div className="space-y-2 p-4 text-sm">
                <p className="flex justify-between"><span className="text-ink-muted">{t("common.currency")}</span><span>{data.currency}</span></p>
                <p className="flex justify-between"><span className="text-ink-muted">{t("common.deadline")}</span><span className="tabular">{date(data.deadline, lang)}</span></p>
                <p className="flex justify-between"><span className="text-ink-muted">{t("rfqs.ledger.incoterm", { defaultValue: "Incoterm" })}</span><span>{data.incoterm}</span></p>
                <p className="flex justify-between"><span className="text-ink-muted">{t("publicForm.payment")}</span><span>{data.payment_terms}</span></p>
              </div>
            </Card>
          </div>
        </TabPanel>

        <TabPanel value="comparison">
          <ComparisonMatrix data={comparison.data} loading={comparison.isLoading} error={comparison.error} onRetry={() => void comparison.refetch()} />
          <ApprovalBar rfq={data} offers={offers} onDone={() => void refresh()} />
        </TabPanel>

        <TabPanel value="negotiation">
          <NegotiationPanel rfq={data} offers={offers} onDone={() => void refresh()} />
        </TabPanel>

        <TabPanel value="extractions">
          <Card className="overflow-hidden">
            <CardHeader title={t("agents.verification")} subtitle={t("summary.verifyHint", { defaultValue: "Supplier replies are parsed into offers; a buyer confirms every extracted field." })} />
            <table className="table-dense">
              <thead>
                <tr>
                  <th>{t("common.supplier")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("requests.confidence")}</th>
                  <th>{t("rfqs.tco")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {(extractions.data?.items ?? []).map((row) => (
                  <tr key={row.id}>
                    <td className="text-sm">{row.supplier_name}</td>
                    <td><StatusChip status={row.status} /></td>
                    <td>
                      <div className="flex w-28 items-center gap-2">
                        <Progress value={row.confidence * 100} tone={row.confidence >= 0.85 ? "ok" : "warn"} />
                        <span className="text-2xs tabular">{percent(row.confidence * 100)}</span>
                      </div>
                    </td>
                    <td className="text-2xs text-ink-muted">{row.issues.length > 0 ? row.issues.join(", ") : "—"}</td>
                    <td>
                      <button type="button" className="btn-secondary text-2xs" onClick={() => setVerifying(row)}>
                        <Eye className="size-3" aria-hidden /> {t("agents.verify")}
                      </button>
                    </td>
                  </tr>
                ))}
                {(extractions.data?.items ?? []).length === 0 ? (
                  <tr>
                    <td colSpan={5}>
                      <EmptyState title={t("common.empty")} description={t("rfqs.messages")} />
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </Card>
        </TabPanel>

        <TabPanel value="messages">
          <Card>
            <CardHeader title={t("rfqs.messages")} subtitle={`${(data.messages ?? []).length}`} />
            <div className="max-h-[28rem] space-y-3 overflow-y-auto p-4">
              {(data.messages ?? []).map((message) => (
                <article
                  key={message.id}
                  className={cn(
                    "max-w-[85%] rounded-lg border px-3 py-2 text-sm",
                    message.direction === "inbound" ? "border-line bg-surface-raised" : "ms-auto border-gold/40 bg-gold/10",
                  )}
                >
                  <p className="mb-1 text-2xs text-ink-subtle">
                    {message.channel} · {message.author_type} · {relative(message.created_at, lang)}
                  </p>
                  <p className="whitespace-pre-wrap text-ink">{message.body}</p>
                </article>
              ))}
              {(data.messages ?? []).length === 0 ? <EmptyState title={t("common.empty")} /> : null}
            </div>
          </Card>
        </TabPanel>
      </Tabs>

      <VerificationDrawer
        extraction={verifying}
        onClose={() => setVerifying(null)}
        onSaved={async () => {
          await refresh();
          setVerifying(null);
        }}
      />
    </div>
  );
}

function ComparisonMatrix({
  data,
  loading,
  error,
  onRetry,
}: {
  data?: Comparison;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const [recommendation, setRecommendation] = useState<Recommendation | null>(null);

  const recommend = useMutation({
    mutationFn: () => api.post<Recommendation>(`/api/v1/rfqs/${data?.rfq.id}/recommend`),
    onSuccess: (result) => setRecommendation(result),
    onError: (err) => toast.push(errorMessage(err), "danger"),
  });

  if (loading) return <LoadingBlock rows={6} />;
  if (error) return <ErrorState message={errorMessage(error)} onRetry={onRetry} />;
  if (!data || data.columns.length === 0) {
    return (
      <Card>
        <EmptyState
          title={t("common.empty")}
          description={t("rfqs.comparison")}
          icon={<FileSpreadsheet className="size-5" aria-hidden />}
        />
      </Card>
    );
  }

  const bestOfferId = data.columns.find((column) => column.is_best_value)?.offer_id ?? data.columns[0]?.offer_id;
  const cellFor = (row: Comparison["rows"][number], offerId: string) => row.cells.find((cell) => cell.offer_id === offerId);

  const summaryRows: { label: string; render: (column: ComparisonColumn) => React.ReactNode }[] = [
    { label: t("rfqs.tco"), render: (column) => <span className="font-semibold tabular">{money(column.tco_total, data.rfq.currency, lang)}</span> },
    { label: t("rfqs.landed"), render: (column) => <span className="tabular">{money(column.landed_cost, data.rfq.currency, lang)}</span> },
    { label: t("common.total"), render: (column) => <span className="tabular">{money(column.subtotal, data.rfq.currency, lang)}</span> },
    { label: t("rfqs.leadTime"), render: (column) => <span className="tabular">{column.lead_time_days != null ? `${num(column.lead_time_days, lang)} ${t("dashboard.days")}` : "—"}</span> },
    { label: t("rfqs.validity"), render: (column) => <span className="tabular">{date(column.validity_date, lang)}</span> },
    { label: t("publicForm.payment"), render: (column) => <span>{column.payment_terms ?? "—"}</span> },
    { label: t("publicForm.incoterm"), render: (column) => <span>{column.incoterm ?? "—"}</span> },
    { label: t("common.status"), render: (column) => <StatusChip status={column.status} /> },
  ];

  return (
    <div className="space-y-3">
      {data.blocked_for_low_confidence ? (
        <div className="flex items-start gap-2 rounded-md border border-gold/40 bg-gold/10 px-3 py-2 text-xs text-ink">
          <AlertTriangle className="mt-0.5 size-4 text-gold" aria-hidden />
          {t("rfqs.lowConfidence")}
        </div>
      ) : null}

      <Card className="overflow-x-auto">
        <CardHeader
          title={t("rfqs.comparison")}
          subtitle={`${data.rfq.code} · ${t("rfqs.tco")} · ${date(data.generated_at, lang)}`}
          action={
            <button type="button" className="btn-secondary" onClick={() => recommend.mutate()} disabled={recommend.isPending}>
              {recommend.isPending ? <Spinner /> : <Sparkles className="size-4" aria-hidden />}
              {t("rfqs.recommend")}
            </button>
          }
        />
        <table className="table-dense min-w-[52rem]">
          <thead>
            <tr>
              <th className="sticky start-0 z-10 bg-surface-alt">{t("common.details")}</th>
              {data.columns.map((column) => (
                <th key={column.offer_id} className="min-w-40 text-start">
                  <div className="flex flex-col gap-1">
                    <span className="text-sm font-semibold text-ink">
                      {lang === "ar" && column.supplier_name_ar ? column.supplier_name_ar : column.supplier_name}
                    </span>
                    <div className="flex flex-wrap items-center gap-1">
                      <Badge tone={column.is_best_value ? "gold" : "neutral"}>{`${t("rfqs.rank")} ${column.rank}`}</Badge>
                      {column.is_low_confidence ? <Badge tone="warn">{t("rfqs.lowConfidenceShort")}</Badge> : null}
                    </div>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={row.rfq_line_id}>
                <td className="sticky start-0 z-[1] bg-surface-alt/95">
                  <p className="text-xs text-ink">{`${row.line_no}. ${row.description}`}</p>
                  <p className="text-2xs text-ink-subtle">
                    {`${num(row.quantity, lang, 2)} ${row.uom}`}
                    {row.target_price ? ` · ${t("rfqs.target")} ${money(row.target_price, data.rfq.currency, lang)}` : ""}
                  </p>
                </td>
                {data.columns.map((column) => {
                  const cell = cellFor(row, column.offer_id);
                  const isBest = cell?.unit_price === row.best_unit_price;
                  return (
                    <td key={column.offer_id} className={cn("align-top text-sm", column.is_best_value && "bg-gold/5")}>
                      {cell ? (
                        <div className="flex flex-col gap-0.5">
                          <span className={cn("tabular", isBest && "font-semibold text-[color:var(--gold-300)]")}>
                            {money(cell.unit_price, cell.currency, lang)}
                            <span className="text-2xs text-ink-subtle">{` / ${row.uom}`}</span>
                          </span>
                          <span className="tabular text-2xs text-ink-subtle">{money(cell.line_total, cell.currency, lang)}</span>
                          <span className="flex items-center gap-1 text-2xs">
                            {cell.verified ? (
                              <Badge tone="ok">
                                <CheckCircle2 className="size-2.5" aria-hidden /> {t("common.yes")}
                              </Badge>
                            ) : (
                              <Badge tone="warn">
                                <Eye className="size-2.5" aria-hidden /> {t("agents.verify")}
                              </Badge>
                            )}
                            {cell.low_confidence ? <Badge tone="danger">?</Badge> : null}
                          </span>
                        </div>
                      ) : (
                        <span className="text-ink-subtle">—</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
            {summaryRows.map((row) => (
              <tr key={row.label}>
                <td className="sticky start-0 z-[1] bg-surface-alt/95 text-xs text-ink-muted">{row.label}</td>
                {data.columns.map((column) => (
                  <td key={column.offer_id} className={cn("text-sm", column.offer_id === bestOfferId && "bg-gold/5")}>
                    {row.render(column)}
                  </td>
                ))}
              </tr>
            ))}
            <tr>
              <td className="sticky start-0 z-[1] bg-surface-alt/95 text-xs text-ink-muted">{t("rfqs.score")}</td>
              {data.columns.map((column) => (
                <td key={column.offer_id}>
                  <div className="w-32">
                    <Progress value={column.weighted_score} tone={column.is_best_value ? "gold" : "info"} />
                    <span className="tabular text-2xs text-ink-subtle">{percent(column.weighted_score)}</span>
                  </div>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </Card>

      <Card>
        <CardHeader title={t("rfqs.weights")} subtitle={t("rfqs.comparison")} />
        <div className="grid gap-2 p-4 sm:grid-cols-5">
          {Object.entries(data.rfq.weights ?? {}).map(([key, weight]) => (
            <div key={key} className="rounded-md border border-line px-2 py-1.5">
              <p className="text-2xs uppercase text-ink-subtle">{key.replaceAll("_", " ")}</p>
              <p className="tabular text-sm">{percent(Number(weight) * 100, 0)}</p>
            </div>
          ))}
        </div>
      </Card>

      {recommendation ? (
        <Card className="p-4">
          <div className="flex items-start gap-3">
            <ThumbsUp className="mt-0.5 size-5 text-gold" aria-hidden />
            <div className="min-w-0">
              <p className="text-sm font-semibold text-ink">
                {t("rfqs.recommend")}: {recommendation.recommended[0]?.supplier_name ?? "—"}
              </p>
              <ol className="mt-1 space-y-1 text-xs text-ink-muted">
                {recommendation.recommended.map((entry) => (
                  <li key={entry.offer_id}>
                    {`#${entry.rank} ${entry.supplier_name} · ${percent(entry.weighted_score)} · ${money(entry.tco_total, data.rfq.currency, lang)}`}
                  </li>
                ))}
              </ol>
              <p className="mt-2 whitespace-pre-wrap text-xs text-ink" dir="auto">
                {lang === "ar" ? recommendation.justification_ar : recommendation.justification_en}
              </p>
              <p className="mt-2 text-2xs text-ink-subtle">
                {`${t("agents.policy")}: ${t("rfqs.policyOk")} · ${t("approvals.subtitle")}`}
                {recommendation.number_violations?.length ? ` · ${recommendation.number_violations.length}` : ""}
              </p>
            </div>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

function ApprovalBar({ rfq, offers, onDone }: { rfq: RFQDetail; offers: Comparison["columns"]; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const navigate = useNavigate();
  const [offerId, setOfferId] = useState(offers[0]?.offer_id ?? "");
  const selected = offers.find((offer) => offer.offer_id === offerId);
  const amount = selected ? Number(selected.tco_total) : 0;

  const request = useMutation({
    mutationFn: () => api.post(`/api/v1/rfqs/${rfq.id}/request-approval`, { amount: String(amount), offer_ids: [offerId] }),
    onSuccess: () => {
      toast.push(t("approvals.title"), "ok");
      onDone();
      navigate("/approvals");
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  if (offers.length === 0) return null;

  return (
    <Card className="mt-3 flex flex-wrap items-end justify-between gap-3 p-4">
      <div className="min-w-48">
        <p className="label">{t("approvals.amount")}</p>
        <select className="input" value={offerId} onChange={(event) => setOfferId(event.target.value)}>
          {offers.map((offer) => (
            <option key={offer.offer_id} value={offer.offer_id}>
              {offer.supplier_name} — {money(offer.tco_total, rfq.currency, lang)}
            </option>
          ))}
        </select>
      </div>
      <div className="text-sm">
        <p className="label">{t("rfqs.tco")}</p>
        <p className="tabular font-semibold">{money(amount, rfq.currency, lang)}</p>
      </div>
      <button type="button" className="btn-primary" onClick={() => request.mutate()} disabled={request.isPending || !offerId}>
        {request.isPending ? <Spinner /> : <ShieldCheck className="size-4" aria-hidden />}
        {t("rfqs.requestApproval")}
      </button>
    </Card>
  );
}

function NegotiationPanel({ rfq, offers, onDone }: { rfq: RFQDetail; offers: Comparison["columns"]; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const [offerId, setOfferId] = useState(offers[0]?.offer_id ?? "");
  const [draft, setDraft] = useState<NegotiationDraft | null>(null);
  const policy = useQuery({ queryKey: ["policy", rfq.id], queryFn: () => api.get<{ policy: NegotiationPolicy | null }>(`/api/v1/rfqs/${rfq.id}/policy`) });
  const [form, setForm] = useState<NegotiationPolicy>({
    target_price: "20000",
    walkaway_price: "23000",
    max_rounds: 3,
    allowed_concessions: ["payment_terms", "lead_time"],
    mode: "semi_auto",
    value_threshold: "500000",
    currency: rfq.currency,
  });

  const current = policy.data?.policy ?? null;
  const effective = useMemo(() => (current ? { ...form, ...current } : form), [current, form]);

  const savePolicy = useMutation({
    mutationFn: () => api.put(`/api/v1/rfqs/${rfq.id}/policy`, effective),
    onSuccess: async () => {
      toast.push(t("rfqs.savePolicy"), "ok");
      await policy.refetch();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const selected = offers.find((offer) => offer.offer_id === offerId);

  const generate = useMutation({
    mutationFn: () =>
      api.post<NegotiationDraft>(`/api/v1/rfqs/${rfq.id}/negotiate`, {
        supplier_id: selected?.supplier_id,
        offer_id: offerId,
      }),
    onSuccess: setDraft,
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const send = useMutation({
    mutationFn: () => api.post(`/api/v1/negotiations/${draft?.message_id}/send`, {}),
    onSuccess: () => {
      toast.push(t("rfqs.send"), "ok");
      setDraft(null);
      onDone();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const decision = draft?.decision;
  const blocked = decision ? !decision.allowed : false;

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader title={t("rfqs.fields.mode")} subtitle={t("rfqs.policyGuard")} action={current ? <Badge tone="ok">{`v${current.version ?? 1}`}</Badge> : <Badge tone="warn">{t("common.none")}</Badge>} />
        <div className="grid grid-cols-2 gap-3 p-4">
          {(
            [
              ["target_price", t("rfqs.fields.targetPrice")],
              ["walkaway_price", t("rfqs.fields.walkaway")],
              ["value_threshold", t("rfqs.fields.valueThreshold")],
              ["max_rounds", t("rfqs.fields.maxRounds")],
            ] as const
          ).map(([key, label]) => (
            <div key={key}>
              <label className="label" htmlFor={key}>
                {label}
              </label>
              <input
                id={key}
                className="input tabular"
                inputMode={key === "max_rounds" ? "numeric" : "decimal"}
                value={String(effective[key])}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, [key]: key === "max_rounds" ? Number(event.target.value) : event.target.value }))
                }
              />
            </div>
          ))}
          <div className="col-span-2">
            <label className="label" htmlFor="mode">
              {t("rfqs.fields.mode")}
            </label>
            <select id="mode" className="input" value={effective.mode} onChange={(event) => setForm((prev) => ({ ...prev, mode: event.target.value as NegotiationPolicy["mode"] }))}>
              <option value="draft_only">{t("rfqs.modes.draft_only")}</option>
              <option value="semi_auto">{t("rfqs.modes.semi_auto")}</option>
              <option value="auto">{t("rfqs.modes.auto")}</option>
            </select>
          </div>
          <div className="col-span-2">
            <p className="label">{t("rfqs.fields.concessions")}</p>
            <div className="flex flex-wrap gap-2">
              {["payment_terms", "lead_time", "packaging", "freight", "volume"].map((concession) => {
                const active = effective.allowed_concessions.includes(concession);
                return (
                  <button
                    key={concession}
                    type="button"
                    className="chip"
                    aria-pressed={active}
                    onClick={() =>
                      setForm((prev) => ({
                        ...prev,
                        allowed_concessions: active
                          ? prev.allowed_concessions.filter((item) => item !== concession)
                          : [...prev.allowed_concessions, concession],
                      }))
                    }
                  >
                    {concession.replaceAll("_", " ")}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
        <div className="flex justify-end border-t border-line px-4 py-3">
          <button type="button" className="btn-primary" onClick={() => savePolicy.mutate()} disabled={savePolicy.isPending}>
            {savePolicy.isPending ? <Spinner /> : null}
            {t("rfqs.savePolicy")}
          </button>
        </div>
      </Card>

      <Card>
        <CardHeader title={t("rfqs.negotiation")} subtitle={t("rfqs.policyGuard")} />
        <div className="space-y-3 p-4">
          <div>
            <label className="label" htmlFor="offer">
              {t("common.supplier")}
            </label>
            <select id="offer" className="input" value={offerId} onChange={(event) => { setOfferId(event.target.value); setDraft(null); }}>
              {offers.map((offer) => (
                <option key={offer.offer_id} value={offer.offer_id}>
                  {offer.supplier_name} — {money(offer.tco_total, rfq.currency, lang)}
                </option>
              ))}
            </select>
          </div>
          <button type="button" className="btn-secondary w-full" onClick={() => generate.mutate()} disabled={!offerId || generate.isPending}>
            {generate.isPending ? <Spinner /> : <Sparkles className="size-4" aria-hidden />}
            {t("rfqs.draft")}
          </button>

          {draft ? (
            <div className="space-y-2">
              <div className={cn("rounded-md border p-2.5 text-xs", blocked ? "border-[color:var(--danger)]/50 bg-[color:var(--danger)]/10" : "border-[color:var(--ok)]/40 bg-[color:var(--ok)]/10")}>
                <p className="flex items-center gap-1.5 font-semibold text-ink">
                  {blocked ? <AlertTriangle className="size-4 text-[color:var(--danger-ink)]" aria-hidden /> : <CheckCircle2 className="size-4 text-[color:var(--ok-ink)]" aria-hidden />}
                  {blocked ? t("rfqs.policyBlocked") : t("rfqs.policyOk")}
                </p>
                {decision?.codes.length ? (
                  <ul className="mt-1 space-y-0.5 font-mono text-2xs text-[color:var(--danger-ink)]">
                    {decision.codes.map((code) => (
                      <li key={code}>{code}</li>
                    ))}
                  </ul>
                ) : null}
                {decision?.reasons.length ? <p className="mt-1 text-2xs text-ink-muted">{decision.reasons.join(" · ")}</p> : null}
                {decision?.warnings.length ? <p className="mt-1 text-2xs text-[color:var(--warn-ink)]">{decision.warnings.join(" · ")}</p> : null}
                {draft.proposed_price ? (
                  <p className="mt-1 tabular text-2xs text-ink-muted">
                    {`${t("rfqs.fields.targetPrice")}: ${money(draft.proposed_price, rfq.currency, lang)}`}
                  </p>
                ) : null}
                {draft.leak_violations?.length ? (
                  <p className="mt-1 text-2xs text-[color:var(--danger-ink)]">{draft.leak_violations.join(" · ")}</p>
                ) : null}
                {draft.injection?.length ? (
                  <p className="mt-1 text-2xs text-[color:var(--warn-ink)]">{`${t("agents.policy")}: ${draft.injection.join(", ")}`}</p>
                ) : null}
              </div>
              <div className="rounded-md border border-line bg-surface p-3">
                <p className="mb-1 flex items-center gap-1.5 text-2xs text-ink-subtle">
                  <Lock className="size-3" aria-hidden /> {t("agents.reasoning")}
                </p>
                <p className="whitespace-pre-wrap text-sm text-ink" dir="auto">
                  {lang === "ar" && draft.body_ar ? draft.body_ar : draft.body_en}
                </p>
              </div>
              <button type="button" className="btn-primary w-full" onClick={() => send.mutate()} disabled={blocked || send.isPending}>
                {send.isPending ? <Spinner /> : <Send className="size-4" aria-hidden />}
                {blocked ? t("rfqs.sendBlocked") : t("rfqs.send")}
              </button>
            </div>
          ) : (
            <EmptyState title={t("rfqs.negotiation")} description={t("rfqs.policyGuard")} icon={<Sparkles className="size-5" />} />
          )}
        </div>
      </Card>
    </div>
  );
}

function VerificationDrawer({ extraction, onClose, onSaved }: { extraction: Extraction | null; onClose: () => void; onSaved: () => Promise<void> }) {
  const { t } = useTranslation();
  const toast = useToast();
  const detail = useQuery({
    queryKey: ["extraction", extraction?.id],
    queryFn: () => api.get<Extraction>(`/api/v1/extractions/${extraction?.id}`),
    enabled: Boolean(extraction?.id),
  });
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const verifyField = async (field: ExtractedField) => {
    if (!extraction) return;
    setBusy(true);
    try {
      await api.post(`/api/v1/extractions/${extraction.id}/fields/${field.id}/verify`, {
        value: edits[field.id] ?? field.value,
      });
      toast.push(`${field.name} ✓`, "ok");
      await detail.refetch();
    } catch (error) {
      toast.push(errorMessage(error), "danger");
    } finally {
      setBusy(false);
    }
  };

  const verifyAll = async () => {
    if (!extraction) return;
    setBusy(true);
    try {
      await api.post(`/api/v1/extractions/${extraction.id}/verify`, {});
      await onSaved();
    } catch (error) {
      toast.push(errorMessage(error), "danger");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer
      open={Boolean(extraction)}
      onClose={onClose}
      title={t("agents.verification")}
      description={extraction?.supplier_name}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {t("common.close")}
          </button>
          <button type="button" className="btn-primary" onClick={() => void verifyAll()} disabled={busy}>
            {busy ? <Spinner /> : <CheckCircle2 className="size-4" aria-hidden />}
            {t("agents.verify")}
          </button>
        </div>
      }
    >
      {detail.isLoading ? (
        <LoadingBlock rows={5} />
      ) : detail.data ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm">
            <StatusChip status={detail.data.status} />
            <span className="tabular text-ink-muted">{percent(detail.data.confidence * 100)}</span>
          </div>
          {detail.data.issues.length > 0 ? (
            <ul className="rounded-md border border-[color:var(--warn)]/40 bg-[color:var(--warn)]/10 p-2 text-2xs text-[color:var(--warn-ink)]">
              {detail.data.issues.map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          ) : null}
          <table className="table-dense">
            <thead>
              <tr>
                <th>{t("common.details")}</th>
                <th>{t("common.price")}</th>
                <th>{t("requests.confidence")}</th>
                <th>{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {(detail.data.fields ?? []).map((field) => (
                <tr key={field.id} className={field.low_confidence ? "bg-[color:var(--warn)]/10" : undefined}>
                  <td className="text-xs text-ink-muted">
                    <p>{field.label}</p>
                    {field.source_span ? (
                      <p className="max-w-56 truncate font-mono text-2xs text-ink-subtle" title={field.source_span} dir="auto">
                        {field.source_span}
                      </p>
                    ) : null}
                  </td>
                  <td>
                    <input
                      className="input py-1 text-sm"
                      value={edits[field.id] ?? field.value}
                      onChange={(event) => setEdits((current) => ({ ...current, [field.id]: event.target.value }))}
                      aria-label={field.label}
                    />
                  </td>
                  <td className="text-2xs tabular">
                    <span className={cn(field.low_confidence && "text-[color:var(--warn-ink)]")}>{percent(field.confidence * 100)}</span>
                    {field.verified_at ? <p className="text-[color:var(--ok-ink)]">{t("common.yes")}</p> : null}
                  </td>
                  <td>
                    <button type="button" className="btn-ghost text-2xs" disabled={busy} onClick={() => void verifyField(field)}>
                      {t("agents.verify")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {detail.data.raw_text ? (
            <details className="rounded-md border border-line p-2">
              <summary className="cursor-pointer text-xs text-ink-muted">{t("rfqs.messages")}</summary>
              <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-2xs text-ink" dir="auto">
                {detail.data.raw_text}
              </pre>
            </details>
          ) : null}
        </div>
      ) : (
        <EmptyState title={t("common.empty")} />
      )}
    </Drawer>
  );
}

/**
 * Export the comparison matrix as CSV. Buyers routinely need this offline for
 * tender committees; building it client-side keeps the export byte-identical to
 * what is on screen and needs no extra server round-trip.
 */
function downloadComparison(comparison: Comparison, filename: string): void {
  const header = ["line", "description", "quantity", "uom", ...comparison.columns.map((column) => column.supplier_name)];
  const rows = comparison.rows.map((row) => [
    String(row.line_no),
    row.description,
    row.quantity,
    row.uom,
    ...comparison.columns.map((column) => row.cells.find((cell) => cell.offer_id === column.offer_id)?.unit_price ?? ""),
  ]);
  const totals = ["", "TCO", "", "", ...comparison.columns.map((column) => column.tco_total)];
  const csv = [header, ...rows, totals]
    .map((line) => line.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(","))
    .join("\r\n");
  const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
