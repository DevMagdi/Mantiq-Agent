import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowUpRight, CircleDollarSign, Clock, FileText, HandCoins, ShieldCheck, Truck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PageHeading } from "@/components/Shell";
import { Card, CardHeader, EmptyState, ErrorState, Kpi, LoadingBlock, StatusChip, Progress } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { compact, date, money, num, percent, relative } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { AgentRun, Approval, Page, RFQSummary } from "@/lib/types";

type DashboardPayload = {
  kpis: {
    period_days: number;
    rfqs_total: number;
    rfqs_open: number;
    offers_total: number;
    purchase_orders: number;
    pending_approvals: number;
    savings: { initial: string; final: string; savings: number; savings_pct: number; savings_amount: string };
    cycle_time_days: number;
    supplier_response_rate?: number;
    avg_offers_per_rfq?: number;
  };
  spend: { by_supplier: { label: string; value: string }[]; by_category: { label: string; value: string }[]; by_plant: { label: string; value: string }[]; total: string };
  price_trends: { period: string; avg_price: number; samples: number }[];
  forecast?: { points: { period: number; forecast: number; lower: number; upper: number }[]; method: string; alpha: number; band: number; based_on: number };
  savings_by_buyer?: { name: string; orders: number; amount: string }[];
  recent_rfqs?: RFQSummary[];
  generated_at?: string;
};

const CHART_COLORS = ["var(--gold)", "#4f8cc9", "#7fb069", "#c96f4f", "#8f7fc9", "#c9a227"];

export default function DashboardPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get<DashboardPayload>("/api/v1/dashboard"),
  });
  const runs = useQuery({
    queryKey: ["agent-runs"],
    queryFn: () => api.get<Page<AgentRun>>("/api/v1/agent-runs?page_size=8"),
  });
  const approvals = useQuery({
    queryKey: ["approvals", "dashboard"],
    queryFn: () => api.get<Page<Approval>>("/api/v1/approvals?status=pending"),
  });

  if (isLoading) return <LoadingBlock rows={8} />;
  if (error || !data) return <ErrorState message={errorMessage(error)} onRetry={() => void refetch()} />;

  const kpis = data.kpis;
  const spendRows = data.spend?.by_category ?? [];
  const trend = data.price_trends ?? [];

  return (
    <div className="space-y-4">
      <PageHeading title={t("dashboard.title")} subtitle={t("dashboard.subtitle")}>
        <p className="mt-1 text-2xs text-ink-subtle">
          {`${t("analytics.period")}: ${num(kpis.period_days, lang)} ${t("dashboard.days")}`}
        </p>
      </PageHeading>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label={t("dashboard.kpis.rfqs")} value={num(kpis.rfqs_open, lang)} hint={`${num(kpis.rfqs_total, lang)} total`} icon={<FileText className="size-4" />} />
        <Kpi label={t("dashboard.kpis.offers")} value={num(kpis.offers_total, lang)} hint={`${num(kpis.avg_offers_per_rfq ?? 0, lang, 1)} / RFQ`} icon={<Truck className="size-4" />} />
        <Kpi
          label={t("dashboard.kpis.savings")}
          value={money(kpis.savings.savings_amount, "EGP", lang)}
          trend={{ value: kpis.savings.savings_pct, label: t("dashboard.kpis.savings") }}
          hint={`${percent(kpis.savings.savings_pct)} vs. budget`}
          icon={<HandCoins className="size-4" />}
        />
        <Kpi
          label={t("dashboard.kpis.approvals")}
          value={num(kpis.pending_approvals, lang)}
          hint={`${num(kpis.purchase_orders, lang)} POs issued`}
          icon={<ShieldCheck className="size-4" />}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title={t("dashboard.spendTitle")} subtitle={t("analytics.spendByCategory")} action={<span className="text-xs text-ink-muted">{money(data.spend?.total, "EGP", lang)}</span>} />
          {spendRows.length === 0 ? (
            <EmptyState title={t("common.empty")} />
          ) : (
            <div className="h-64 p-3" dir="ltr">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={spendRows.map((row) => ({ label: row.label, value: Number(row.value) }))}>
                  <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="label" stroke="var(--ink-subtle)" fontSize={11} tickLine={false} />
                  <YAxis stroke="var(--ink-subtle)" fontSize={11} tickFormatter={(value: number) => compact(value, "en")} tickLine={false} />
                  <Tooltip
                    contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 }}
                    formatter={(value: number) => money(value, "EGP", "en")}
                  />
                  <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                    {spendRows.map((row, index) => (
                      <Cell key={row.label} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title={t("dashboard.kpis.cycle")} subtitle={t("analytics.forecast")} />
          <div className="space-y-4 p-4">
            <div>
              <p className="text-3xl font-semibold tabular text-ink">{num(kpis.cycle_time_days, lang, 1)}</p>
              <p className="text-xs text-ink-muted">{t("dashboard.days")}</p>
            </div>
            <div>
              <div className="mb-1 flex justify-between text-xs text-ink-muted">
                <span>{t("dashboard.kpis.suppliers")}</span>
                <span>{percent(kpis.supplier_response_rate ?? 0)}</span>
              </div>
              <Progress value={kpis.supplier_response_rate ?? 0} tone="info" />
            </div>
            <div className="rounded-md border border-line bg-surface p-3 text-xs text-ink-muted">
              <p className="flex items-center gap-1.5 font-medium text-ink">
                <CircleDollarSign className="size-4 text-gold" aria-hidden /> {money(kpis.savings.final, "EGP", lang)}
              </p>
              <p className="mt-1">
                {`${money(kpis.savings.initial, "EGP", lang)} → ${money(kpis.savings.final, "EGP", lang)}`}
              </p>
              <p className="mt-1 text-[color:var(--ok)]">{`${percent(kpis.savings.savings_pct)} ${t("dashboard.savingsTitle")}`}</p>
            </div>
          </div>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title={t("dashboard.trendTitle")} subtitle={t("analytics.trends")} />
          {trend.length === 0 ? (
            <EmptyState title={t("common.empty")} />
          ) : (
            <div className="h-56 p-3" dir="ltr">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trend}>
                  <defs>
                    <linearGradient id="gold" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--gold)" stopOpacity={0.45} />
                      <stop offset="100%" stopColor="var(--gold)" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="period" stroke="var(--ink-subtle)" fontSize={11} tickLine={false} />
                  <YAxis stroke="var(--ink-subtle)" fontSize={11} tickFormatter={(value: number) => compact(value, "en")} tickLine={false} />
                  <Tooltip
                    contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 }}
                  />
                  <Area type="monotone" dataKey="avg_price" stroke="var(--gold)" fill="url(#gold)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card>
          <CardHeader
            title={t("dashboard.recentRfqs")}
            action={
              <Link to="/rfqs" className="text-2xs text-gold hover:underline">
                {t("dashboard.viewAll")}
              </Link>
            }
          />
          <div className="divide-y divide-line/60">
            {(data.recent_rfqs ?? []).slice(0, 5).map((rfq) => (
              <Link key={rfq.id} to={`/rfqs/${rfq.id}`} className="flex items-center justify-between gap-2 px-4 py-2.5 hover:bg-surface-raised">
                <div className="min-w-0">
                  <p className="truncate text-sm text-ink">{rfq.title}</p>
                  <p className="text-2xs text-ink-subtle">{rfq.code} · {date(rfq.deadline, lang)}</p>
                </div>
                <StatusChip status={rfq.status} label={t(`rfqs.columns.${rfq.status === "closed" ? "closed" : rfq.status === "draft" ? "draft" : rfq.status === "sent" ? "sent" : rfq.status === "replied" ? "replied" : "negotiating"}`)} />
              </Link>
            ))}
            {(data.recent_rfqs ?? []).length === 0 ? <EmptyState title={t("common.empty")} /> : null}
          </div>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title={t("analytics.savingsByBuyer")} subtitle={t("dashboard.savingsTitle")} />
          <table className="table-dense">
            <thead>
              <tr>
                <th>{t("common.buyers")}</th>
                <th className="text-end">{t("dashboard.kpis.offers")}</th>
                <th className="text-end">{t("common.total")}</th>
              </tr>
            </thead>
            <tbody>
              {(data.savings_by_buyer ?? []).map((row) => (
                <tr key={row.name}>
                  <td className="text-sm">{row.name}</td>
                  <td className="text-end tabular text-sm">{num(row.orders, lang)}</td>
                  <td className="text-end tabular text-sm">{money(row.amount, "EGP", lang)}</td>
                </tr>
              ))}
              {(data.savings_by_buyer ?? []).length === 0 ? (
                <tr>
                  <td colSpan={3}>
                    <EmptyState title={t("common.empty")} />
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
          {data.forecast?.points.length ? (
            <div className="border-t border-line px-4 py-3">
              <p className="label">{`${t("analytics.forecast")} · ${data.forecast.method}`}</p>
              <div className="grid gap-2 sm:grid-cols-3">
                {data.forecast.points.map((point) => (
                  <div key={point.period} className="rounded-md border border-line px-2 py-1.5 text-xs">
                    <p className="text-ink-muted">{`T+${point.period}`}</p>
                    <p className="tabular font-semibold text-ink">{num(point.forecast, lang)}</p>
                    <p className="text-2xs text-ink-subtle">{`${num(point.lower, lang)} – ${num(point.upper, lang)}`}</p>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </Card>

        <Card>
          <CardHeader
            title={t("dashboard.activity")}
            action={
              <Link to="/activity" className="text-2xs text-gold hover:underline">
                {t("dashboard.viewAll")}
              </Link>
            }
          />
          <div className="divide-y divide-line/60">
            {(runs.data?.items ?? []).slice(0, 6).map((run) => (
              <div key={run.id} className="flex items-start gap-3 px-4 py-2.5">
                <Activity className="mt-0.5 size-4 text-gold" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-ink">{run.input_summary || run.kind}</p>
                  <p className="truncate text-2xs text-ink-subtle">{run.output_summary}</p>
                </div>
                <span className="text-2xs text-ink-subtle">{relative(run.created_at, lang)}</span>
              </div>
            ))}
            {(runs.data?.items ?? []).length === 0 ? <EmptyState title={t("common.empty")} /> : null}
          </div>
        </Card>

        <Card>
          <CardHeader
            title={t("approvals.title")}
            action={
              <Link to="/approvals" className="text-2xs text-gold hover:underline">
                {t("dashboard.viewAll")}
              </Link>
            }
          />
          <div className="divide-y divide-line/60">
            {(approvals.data?.items ?? []).slice(0, 5).map((approval) => (
              <div key={approval.id} className="flex items-center justify-between gap-2 px-4 py-2.5">
                <div className="min-w-0">
                  <p className="truncate text-sm text-ink">{approval.rfq_title}</p>
                  <p className="text-2xs text-ink-subtle">
                    {approval.rfq_code} · {t("approvals.step")} {approval.step_no} · {approval.approver_role}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {approval.due_at ? (
                    <span className="flex items-center gap-1 text-2xs text-ink-subtle">
                      <Clock className="size-3" aria-hidden /> {relative(approval.due_at, lang)}
                    </span>
                  ) : null}
                  <StatusChip status={approval.status} />
                </div>
              </div>
            ))}
            {(approvals.data?.items ?? []).length === 0 ? <EmptyState title={t("approvals.empty")} /> : null}
          </div>
          <div className="border-t border-line px-4 py-2 text-end">
            <Link to="/analytics" className="inline-flex items-center gap-1 text-2xs text-gold hover:underline">
              {t("nav.analytics")} <ArrowUpRight className="size-3" aria-hidden />
            </Link>
          </div>
        </Card>
      </div>
    </div>
  );
}
