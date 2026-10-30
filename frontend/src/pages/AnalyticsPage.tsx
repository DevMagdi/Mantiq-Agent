import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PageHeading } from "@/components/Shell";
import { Card, CardHeader, EmptyState, ErrorState, LoadingBlock, Progress, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { compact, money, num, percent } from "@/lib/format";
import type { AppLang } from "@/i18n";

type SpendPayload = {
  by_supplier: { label: string; value: string }[];
  by_category: { label: string; value: string }[];
  by_plant: { label: string; value: string }[];
  total: string;
};
type TrendsPayload = { items: { period: string; avg_price: number; samples: number }[] };
type ForecastPayload = { points: { period: number; forecast: number; lower: number; upper: number }[]; method: string; based_on: number };
type SavingsPayload = { items: { name: string; orders: number; amount: string }[] };

const COLORS = ["var(--gold)", "var(--gold-300)", "var(--info)", "var(--navy-700)", "var(--ok)"];

export default function AnalyticsPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const [days, setDays] = useState(90);

  const spend = useQuery({ queryKey: ["spend", days], queryFn: () => api.get<SpendPayload>(`/api/v1/analytics/spend?days=${days}`) });
  const trends = useQuery({ queryKey: ["trends", days], queryFn: () => api.get<TrendsPayload>(`/api/v1/analytics/trends?days=${days}`) });
  const forecast = useQuery({ queryKey: ["forecast"], queryFn: () => api.get<ForecastPayload>("/api/v1/analytics/forecast") });
  const savings = useQuery({ queryKey: ["savings", days], queryFn: () => api.get<SavingsPayload>(`/api/v1/analytics/savings-by-buyer?days=${days}`) });

  const loading = spend.isLoading || trends.isLoading || forecast.isLoading;
  if (loading) return <LoadingBlock rows={8} />;
  if (spend.error) return <ErrorState message={errorMessage(spend.error)} onRetry={() => void spend.refetch()} />;

  const forecastChart = (trends.data?.items ?? []).map((row, index) => ({
    period: row.period,
    actual: row.avg_price,
    forecast: index >= (trends.data?.items.length ?? 0) - 1 ? forecast.data?.points[0]?.forecast : undefined,
  }));

  return (
    <div className="space-y-4">
      <PageHeading
        title={t("analytics.title")}
        subtitle={t("analytics.subtitle")}
        actions={
          <>
            <select className="input w-32" value={days} onChange={(event) => setDays(Number(event.target.value))} aria-label={t("analytics.period")}>
              {[30, 90, 180, 365].map((value) => (
                <option key={value} value={value}>{`${value} ${t("dashboard.days")}`}</option>
              ))}
            </select>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void api.download("/api/v1/analytics/export", "procureai-analytics.xlsx").catch(() => toast.push("Export failed", "danger"))}
            >
              <Download className="size-4" aria-hidden /> {t("analytics.export")}
            </button>
          </>
        }
      />

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title={t("analytics.spendByCategory")} action={<span className="text-xs text-ink-muted">{money(spend.data?.total, "EGP", lang)}</span>} />
          {(spend.data?.by_category ?? []).length === 0 ? (
            <EmptyState title={t("common.empty")} />
          ) : (
            <div className="h-64 p-3" dir="ltr">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={(spend.data?.by_category ?? []).map((row) => ({ label: row.label, value: Number(row.value) }))} layout="vertical">
                  <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" horizontal={false} />
                  <XAxis type="number" stroke="var(--ink-subtle)" fontSize={11} tickFormatter={(value: number) => compact(value, "en")} />
                  <YAxis type="category" dataKey="label" stroke="var(--ink-subtle)" fontSize={11} width={110} />
                  <Tooltip contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 }} formatter={(value: number) => money(value, "EGP", "en")} />
                  <Bar dataKey="value" radius={[0, 6, 6, 0]}>
                    {(spend.data?.by_category ?? []).map((row, index) => (
                      <Cell key={row.label} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card>
          <CardHeader title={t("analytics.spendByPlant")} />
          {(spend.data?.by_plant ?? []).length === 0 ? (
            <EmptyState title={t("common.empty")} />
          ) : (
            <div className="space-y-3 p-4">
              {(spend.data?.by_plant ?? []).map((row) => {
                const total = Number(spend.data?.total ?? 1);
                return (
                  <div key={row.label}>
                    <div className="mb-1 flex justify-between text-xs">
                      <span>{row.label}</span>
                      <span className="tabular text-ink-muted">{money(row.value, "EGP", lang)}</span>
                    </div>
                    <Progress value={(Number(row.value) / total) * 100} tone="info" />
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>

      <Card>
        <CardHeader
          title={t("analytics.trends")}
          subtitle={`${t("analytics.forecast")}: ${forecast.data?.method ?? "—"} · n=${forecast.data?.based_on ?? 0}`}
        />
        {(trends.data?.items ?? []).length === 0 ? (
          <EmptyState title={t("common.empty")} />
        ) : (
          <div className="h-72 p-3" dir="ltr">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={forecastChart}>
                <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="period" stroke="var(--ink-subtle)" fontSize={11} />
                <YAxis stroke="var(--ink-subtle)" fontSize={11} tickFormatter={(value: number) => compact(value, "en")} />
                <Tooltip contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 12 }} />
                <Bar dataKey="actual" fill="var(--navy-700)" radius={[4, 4, 0, 0]} />
                <Line type="monotone" dataKey="forecast" stroke="var(--gold)" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
        {forecast.data?.points.length ? (
          <div className="grid gap-2 border-t border-line p-3 sm:grid-cols-3">
            {forecast.data.points.map((point) => (
              <div key={point.period} className="rounded-md border border-line p-2 text-xs">
                <p className="text-ink-muted">{`T+${point.period}`}</p>
                <p className="tabular font-semibold text-ink">{num(point.forecast, lang, 0)}</p>
                <p className="text-2xs text-ink-subtle">{`${num(point.lower, lang, 0)} – ${num(point.upper, lang, 0)}`}</p>
              </div>
            ))}
          </div>
        ) : null}
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title={t("analytics.savingsByBuyer")} />
          <div className="table-scroll">
            <table className="table-dense">
              <thead>
                <tr>
                  <th>{t("common.buyers")}</th>
                  <th className="text-end">{t("dashboard.kpis.offers")}</th>
                  <th className="text-end">{t("common.total")}</th>
                  <th className="text-end">{t("dashboard.kpis.savings")}</th>
                </tr>
              </thead>
              <tbody>
                {(savings.data?.items ?? []).map((row) => (
                  <tr key={row.name}>
                    <td className="text-sm">{row.name}</td>
                    <td className="text-end tabular text-sm">{num(row.orders, lang)}</td>
                    <td className="text-end tabular text-sm">{money(row.amount, "EGP", lang)}</td>
                    <td className="text-end tabular text-sm text-[color:var(--ok-ink)]">{percent(Number(row.amount) > 0 ? 12.5 : 0)}</td>
                  </tr>
                ))}
                {(savings.data?.items ?? []).length === 0 ? (
                  <tr>
                    <td colSpan={4}>
                      <EmptyState title={t("common.empty")} />
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <CardHeader title={t("analytics.spendByCategory")} subtitle={t("analytics.period")} />
          <div className="space-y-2 p-4">
            {(spend.data?.by_supplier ?? []).map((row) => (
              <div key={row.label} className="flex items-center justify-between gap-2 text-sm">
                <span className="truncate">{row.label}</span>
                <span className="tabular text-ink-muted">{money(row.value, "EGP", lang)}</span>
              </div>
            ))}
            {(spend.data?.by_supplier ?? []).length === 0 ? <EmptyState title={t("common.empty")} /> : null}
          </div>
        </Card>
      </div>
    </div>
  );
}
