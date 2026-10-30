import { useQuery } from "@tanstack/react-query";
import { Bot, CheckCircle2, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeading } from "@/components/Shell";
import { Badge, Card, CardHeader, Drawer, EmptyState, ErrorState, LoadingBlock, Progress, Spinner, StatusChip } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/session";
import { PERM } from "@/lib/permissions";
import { dateTime, money, num } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { AgentRun, Page } from "@/lib/types";

export default function ActivityPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const [selected, setSelected] = useState<AgentRun | null>(null);

  const { can } = useAuth();
  const allowed = can(PERM.auditRead);
  const runs = useQuery({
    queryKey: ["agent-runs"],
    queryFn: () => api.get<Page<AgentRun>>("/api/v1/agent-runs?page_size=50"),
    enabled: allowed,
  });

  const detail = useQuery({
    queryKey: ["agent-run", selected?.id],
    queryFn: () => api.get<AgentRun>(`/api/v1/agent-runs/${selected?.id}`),
    enabled: Boolean(selected?.id),
  });

  const tools = useQuery({
    queryKey: ["agent-tools"],
    queryFn: () => api.get<{ items: { name: string; description: string; mutating: boolean; permission: string }[] }>("/api/v1/agent/tools"),
    enabled: allowed,
  });

  if (runs.isLoading) return <LoadingBlock rows={8} />;
  if (runs.error) return <ErrorState message={errorMessage(runs.error)} onRetry={() => void runs.refetch()} />;

  return (
    <div className="space-y-4">
      <PageHeading title={t("agents.title")} subtitle={t("agents.subtitle")} />

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2 overflow-hidden">
          <CardHeader title={t("agents.title")} subtitle={`${runs.data?.items.length ?? 0}`} />
          <div className="max-h-[32rem] divide-y divide-line/60 overflow-y-auto">
            {(runs.data?.items ?? []).map((run) => {
              const decision = (run.policy_decision ?? {}) as { allowed?: boolean; codes?: string[] };
              return (
                <button key={run.id} type="button" className="flex w-full items-start gap-3 px-4 py-3 text-start hover:bg-surface-raised" onClick={() => setSelected(run)}>
                  <Bot className="mt-0.5 size-4 text-gold" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-ink">{run.kind}</span>
                      <StatusChip status={run.status} />
                      {decision.allowed === false ? <Badge tone="danger">{t("rfqs.policyBlocked")}</Badge> : <Badge tone="ok">{t("rfqs.policyOk")}</Badge>}
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-xs text-ink-muted">{run.input_summary}</p>
                    <p className="mt-0.5 text-2xs text-ink-subtle">
                      {run.model} · {dateTime(run.created_at, lang)} · {num(run.latency_ms ?? 0, lang)} ms · {money(run.cost_usd ?? 0, "USD", lang)}
                    </p>
                  </div>
                  <span className="text-2xs text-ink-subtle">{run.steps?.length ?? 0} {t("agents.tools")}</span>
                </button>
              );
            })}
            {(runs.data?.items ?? []).length === 0 ? <EmptyState title={t("common.empty")} /> : null}
          </div>
        </Card>

        <Card>
          <CardHeader title={t("agents.tools")} subtitle={`${tools.data?.items.length ?? 0}`} />
          <ul className="max-h-[32rem] divide-y divide-line/60 overflow-y-auto">
            {(tools.data?.items ?? []).map((tool) => (
              <li key={tool.name} className="px-4 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-xs text-gold">{tool.name}</span>
                  <Badge tone={tool.mutating ? "warn" : "neutral"}>{tool.mutating ? "mutating" : "read"}</Badge>
                </div>
                <p className="text-2xs text-ink-muted">{tool.description}</p>
                <p className="text-2xs text-ink-subtle">{tool.permission}</p>
              </li>
            ))}
            {(tools.data?.items ?? []).length === 0 ? <EmptyState title={t("common.empty")} /> : null}
          </ul>
        </Card>
      </div>

      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected?.kind ?? ""}
        description={selected?.id}
        width="max-w-2xl"
      >
        {detail.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-ink-muted">
            <Spinner /> {t("common.loading")}
          </div>
        ) : detail.data ? (
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="label">{t("common.status")}</p>
                <StatusChip status={detail.data.status} />
              </div>
              <div>
                <p className="label">{t("agents.tokens")}</p>
                <p className="tabular">{num(detail.data.tokens_in, lang)} → {num(detail.data.tokens_out, lang)}</p>
              </div>
              <div>
                <p className="label">{t("agents.cost")}</p>
                <p className="tabular">{money(detail.data.cost_usd ?? 0, "USD", lang)}</p>
              </div>
              <div>
                <p className="label">{t("agents.latency")}</p>
                <p className="tabular">{num(detail.data.latency_ms ?? 0, lang)} ms</p>
              </div>
            </div>

            <section>
              <p className="label">{t("agents.reasoning")}</p>
              <p className="whitespace-pre-wrap rounded-md border border-line bg-surface p-2 text-xs" dir="auto">
                {detail.data.reasoning || detail.data.output_summary}
              </p>
            </section>

            <section>
              <p className="label">{t("agents.policy")}</p>
              <pre className="max-h-40 overflow-auto rounded-md border border-line bg-surface p-2 text-2xs">
                {JSON.stringify(detail.data.policy_decision ?? {}, null, 2)}
              </pre>
            </section>

            <section>
              <p className="label">{t("agents.tools")}</p>
              <ol className="space-y-1">
                {(detail.data.steps ?? []).map((step) => (
                  <li key={step.id} className="flex items-center justify-between rounded border border-line px-2 py-1 text-xs">
                    <span className="flex items-center gap-2">
                      <CheckCircle2 className="size-3 text-[color:var(--ok-ink)]" aria-hidden />
                      {step.step_no}. {step.node}
                    </span>
                    <span className="text-2xs text-ink-subtle">{num(step.latency_ms ?? 0, lang)} ms</span>
                  </li>
                ))}
                {(detail.data.steps ?? []).length === 0 ? <p className="text-xs text-ink-muted">{t("common.empty")}</p> : null}
              </ol>
            </section>

            <section className="rounded-md border border-line p-2">
              <p className="mb-1 flex items-center gap-1.5 text-xs text-ink-muted">
                <ShieldCheck className="size-3 text-gold" aria-hidden /> {t("agents.verification")}
              </p>
              <Progress value={100} tone="ok" />
            </section>
          </div>
        ) : (
          <EmptyState title={t("common.empty")} />
        )}
      </Drawer>
    </div>
  );
}
