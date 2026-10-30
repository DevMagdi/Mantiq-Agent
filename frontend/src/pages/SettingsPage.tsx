import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plug, RefreshCw, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeading } from "@/components/Shell";
import { Badge, Card, CardHeader, EmptyState, ErrorState, LoadingBlock, Progress, Spinner, StatusChip, TabList, TabPanel, Tabs, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, money, num } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { ApiKey, Integration, IntegrationsPayload, Page, Usage } from "@/lib/types";

type Capabilities = {
  adapters: Record<string, { provider: string; live: boolean; mode: string }>;
  features: Record<string, boolean>;
};
type LlmConfigRow = {
  id: string;
  provider: string;
  model: string;
  base_url?: string;
  is_default: boolean;
  is_self_hosted: boolean;
  monthly_token_budget: number;
  tokens_used: number;
  has_key: boolean;
};
type LlmConfigPayload = { items: LlmConfigRow[]; platform_default: { provider: string; model: string } };
type RetentionPayload = { items: { id: string; entity: string; retain_days: number }[]; defaults: Record<string, number> };
type Invoice = { id: string; number: string; amount: string; currency: string; status: string; issued_at: string; paid_at?: string | null };

const RETENTION_ENTITIES = ["procurement_records", "audit_logs", "supplier_pii", "llm_transcripts"];

export default function SettingsPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState("integrations");
  const { i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;

  return (
    <div className="space-y-4">
      <PageHeading title={t("settings.title")} subtitle={t("settings.subtitle")} />
      <Tabs value={tab} onValueChange={setTab}>
        <TabList
          items={[
            { value: "integrations", label: t("settings.integrations") },
            { value: "apiKeys", label: t("settings.apiKeys") },
            { value: "llm", label: t("settings.llm") },
            { value: "billing", label: t("settings.billing") },
            { value: "retention", label: t("settings.retention") },
          ]}
        />
        <TabPanel value="integrations"><Integrations lang={lang} /></TabPanel>
        <TabPanel value="apiKeys"><ApiKeys lang={lang} /></TabPanel>
        <TabPanel value="llm"><LlmSettings lang={lang} /></TabPanel>
        <TabPanel value="billing"><Billing lang={lang} /></TabPanel>
        <TabPanel value="retention"><Retention /></TabPanel>
      </Tabs>
    </div>
  );
}

function Integrations({ lang }: { lang: AppLang }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [testing, setTesting] = useState<string | null>(null);
  const [form, setForm] = useState({ kind: "email", name: "", config: "{}" });
  const [saving, setSaving] = useState(false);

  const capabilities = useQuery({ queryKey: ["capabilities"], queryFn: () => api.get<Capabilities>("/api/v1/meta/capabilities") });
  const integrations = useQuery({ queryKey: ["integrations"], queryFn: () => api.get<IntegrationsPayload>("/api/v1/integrations") });

  const test = async (kind: string) => {
    setTesting(kind);
    try {
      const result = await api.post<{ ok: boolean; provider?: string; model?: string; error?: string; note?: string }>(`/api/v1/integrations/${kind}/test`);
      toast.push(`${kind}: ${result.ok ? result.model ?? result.provider ?? "OK" : result.error ?? "failed"}`, result.ok ? "ok" : "danger");
      await queryClient.invalidateQueries({ queryKey: ["integrations"] });
    } catch (error) {
      toast.push(errorMessage(error), "danger");
    } finally {
      setTesting(null);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      let config: Record<string, unknown> = {};
      if (form.config.trim()) config = JSON.parse(form.config) as Record<string, unknown>;
      await api.post("/api/v1/integrations", { kind: form.kind, name: form.name, config });
      await queryClient.invalidateQueries({ queryKey: ["integrations"] });
      toast.push(t("common.save"), "ok");
    } catch (error) {
      toast.push(errorMessage(error), "danger");
    } finally {
      setSaving(false);
    }
  };

  if (capabilities.isLoading || integrations.isLoading) return <LoadingBlock rows={6} />;
  if (capabilities.error) return <ErrorState message={errorMessage(capabilities.error)} onRetry={() => void capabilities.refetch()} />;
  if (integrations.error) return <ErrorState message={errorMessage(integrations.error)} onRetry={() => void integrations.refetch()} />;

  const rows = Object.entries(capabilities.data?.adapters ?? {});
  const platform = integrations.data?.platform_adapters ?? {};

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader title={t("settings.integrations")} subtitle={t("settings.status")} />
        <ul className="divide-y divide-line/60">
          {rows.map(([kind, adapter]) => (
            <li key={kind} className="flex items-center justify-between gap-2 px-4 py-3">
              <div>
                <p className="flex items-center gap-2 text-sm font-medium text-ink">
                  <Plug className="size-4 text-gold" aria-hidden /> {kind}
                </p>
                <p className="text-2xs text-ink-subtle">{adapter.provider} · {adapter.mode}</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={adapter.live ? "ok" : "warn"}>{adapter.live ? "live" : "sandbox"}</Badge>
                <button type="button" className="btn-ghost text-2xs" onClick={() => void test(kind)} disabled={testing === kind}>
                  {testing === kind ? <Spinner /> : <RefreshCw className="size-3" aria-hidden />}
                  {t("settings.test")}
                </button>
              </div>
            </li>
          ))}
          {rows.length === 0 ? <EmptyState title={t("common.empty")} /> : null}
        </ul>
      </Card>

      <div className="space-y-4">
        <Card>
          <CardHeader title={`${t("settings.integrations")} · ${t("common.details")}`} />
          <div className="space-y-3 p-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label" htmlFor="integration-kind">Kind</label>
                <select id="integration-kind" className="input" value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
                  {Object.keys(platform).map((kind) => (
                    <option key={kind} value={kind}>{kind}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="label" htmlFor="integration-name">{t("common.details")}</label>
                <input id="integration-name" className="input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Production SMTP" />
              </div>
            </div>
            <div>
              <label className="label" htmlFor="integration-config">Config (JSON)</label>
              <textarea
                id="integration-config"
                className="input min-h-24 font-mono text-xs"
                dir="ltr"
                value={form.config}
                onChange={(event) => setForm({ ...form, config: event.target.value })}
                aria-describedby="integration-config-hint"
              />
              <p id="integration-config-hint" className="mt-1 text-2xs text-ink-subtle">
                {t("auth.trust")} · {t("settings.keyOnce")}
              </p>
            </div>
            <div className="flex justify-end">
              <button type="button" className="btn-primary" onClick={() => void save()} disabled={saving}>
                {saving ? <Spinner /> : <Save className="size-4" aria-hidden />}
                {t("common.save")}
              </button>
            </div>
          </div>
        </Card>

        <Card>
          <CardHeader title={t("settings.status")} subtitle={`${integrations.data?.items.length ?? 0}`} />
          <table className="table-dense">
            <thead>
              <tr>
                <th>{t("settings.integrations")}</th>
                <th>Config</th>
                <th>{t("common.status")}</th>
                <th>{t("common.date")}</th>
              </tr>
            </thead>
            <tbody>
              {(integrations.data?.items ?? []).map((row: Integration) => (
                <tr key={row.id}>
                  <td className="text-sm">{row.kind}</td>
                  <td className="max-w-40 truncate text-2xs text-ink-muted" dir="ltr">{JSON.stringify(row.config ?? {})}</td>
                  <td><StatusChip status={row.status ?? "configured"} /></td>
                  <td className="text-2xs text-ink-subtle">{date(row.last_tested_at, lang)}</td>
                </tr>
              ))}
              {(integrations.data?.items ?? []).length === 0 ? (
                <tr>
                  <td colSpan={4}>
                    <EmptyState title={t("common.empty")} description={t("settings.integrations")} />
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}

function ApiKeys({ lang }: { lang: AppLang }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState("read");
  const [created, setCreated] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => api.get<Page<ApiKey>>("/api/v1/api-keys") });

  const create = async () => {
    setBusy(true);
    try {
      const result = await api.post<{ id: string; api_key: string; note: string }>("/api/v1/api-keys", {
        name,
        scopes: scopes.split(",").map((item) => item.trim()).filter(Boolean),
      });
      setCreated(result.api_key);
      setName("");
      await queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    } catch (error) {
      toast.push(errorMessage(error), "danger");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: string) => {
    try {
      await api.delete(`/api/v1/api-keys/${id}`);
      await queryClient.invalidateQueries({ queryKey: ["api-keys"] });
      toast.push(t("common.confirm"), "ok");
    } catch (error) {
      toast.push(errorMessage(error), "danger");
    }
  };

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader title={t("settings.apiKeys")} subtitle={t("settings.keyOnce")} />
        <div className="space-y-3 p-4">
          <div>
            <label className="label" htmlFor="key-name">{t("common.details")}</label>
            <input id="key-name" className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="ERP integration" />
          </div>
          <div>
            <label className="label" htmlFor="key-scopes">Scopes</label>
            <input id="key-scopes" className="input" dir="ltr" value={scopes} onChange={(event) => setScopes(event.target.value)} placeholder="read, write" />
          </div>
          <button type="button" className="btn-primary" disabled={!name.trim() || busy} onClick={() => void create()}>
            {busy ? <Spinner /> : <KeyRound className="size-4" aria-hidden />}
            {t("settings.createKey")}
          </button>
          {created ? (
            <div className="rounded-md border border-gold/50 bg-gold/10 p-3">
              <p className="text-2xs text-ink-muted">{t("settings.keyOnce")}</p>
              <div className="mt-1 flex items-center gap-2">
                <code className="flex-1 break-all rounded bg-surface px-2 py-1 text-2xs" dir="ltr">{created}</code>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => void navigator.clipboard.writeText(created).then(() => toast.push("Copied", "ok"))}
                  aria-label="Copy API key"
                >
                  <Copy className="size-4" />
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </Card>

      <Card>
        <CardHeader title={t("settings.apiKeys")} subtitle={`${keys.data?.items.length ?? 0}`} />
        {keys.error ? (
          <ErrorState message={errorMessage(keys.error)} onRetry={() => void keys.refetch()} />
        ) : (
          <table className="table-dense">
            <thead>
              <tr>
                <th>{t("common.details")}</th>
                <th>Prefix</th>
                <th>{t("common.status")}</th>
                <th>{t("common.date")}</th>
                <th>{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {(keys.data?.items ?? []).map((key) => (
                <tr key={key.id}>
                  <td className="text-sm">{key.name}</td>
                  <td className="font-mono text-xs" dir="ltr">{key.prefix}…</td>
                  <td><Badge tone={key.revoked_at ? "danger" : "ok"}>{key.revoked_at ? "revoked" : "active"}</Badge></td>
                  <td className="text-2xs">{date(key.created_at, lang)}</td>
                  <td>
                    {key.revoked_at ? null : (
                      <button type="button" className="btn-ghost text-2xs text-[color:var(--danger)]" onClick={() => void revoke(key.id)}>
                        {t("common.cancel")}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {(keys.data?.items ?? []).length === 0 ? (
                <tr>
                  <td colSpan={5}>
                    <EmptyState title={t("common.empty")} />
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function LlmSettings({ lang }: { lang: AppLang }) {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const config = useQuery({ queryKey: ["llm-config"], queryFn: () => api.get<LlmConfigPayload>("/api/v1/llm-config") });
  const [form, setForm] = useState({ provider: "fake", model: "", base_url: "", api_key: "", is_self_hosted: false, monthly_token_budget: 5_000_000 });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!config.data) return;
    const current = config.data.items.find((row) => row.is_default);
    setForm((state) => ({
      ...state,
      provider: current?.provider ?? config.data.platform_default.provider,
      model: current?.model ?? config.data.platform_default.model,
      base_url: current?.base_url ?? "",
      monthly_token_budget: current?.monthly_token_budget ?? state.monthly_token_budget,
    }));
  }, [config.data]);

  const save = async () => {
    setBusy(true);
    try {
      await api.put("/api/v1/llm-config", { ...form, is_self_hosted: form.provider === "self_hosted" });
      await queryClient.invalidateQueries({ queryKey: ["llm-config"] });
      await queryClient.invalidateQueries({ queryKey: ["capabilities"] });
      toast.push(t("common.save"), "ok");
    } catch (error) {
      toast.push(errorMessage(error), "danger");
    } finally {
      setBusy(false);
    }
  };

  if (config.isLoading) return <LoadingBlock rows={4} />;
  if (config.error) return <ErrorState message={errorMessage(config.error)} onRetry={() => void config.refetch()} />;

  return (
    <Card>
      <CardHeader
        title={t("settings.llm")}
        subtitle={`${config.data?.platform_default.provider ?? "—"} · ${config.data?.platform_default.model ?? "—"}`}
      />
      <div className="grid gap-3 p-4 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="provider">Provider</label>
          <select id="provider" className="input" value={form.provider} onChange={(event) => setForm({ ...form, provider: event.target.value })}>
            {["fake", "anthropic", "openai_compatible", "self_hosted"].map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="model">Model</label>
          <input id="model" className="input" dir="ltr" value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="base-url">Base URL</label>
          <input id="base-url" className="input" dir="ltr" value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} placeholder="https://api.anthropic.com" />
        </div>
        <div>
          <label className="label" htmlFor="api-key">API key</label>
          <input
            id="api-key"
            type="password"
            className="input"
            dir="ltr"
            autoComplete="off"
            value={form.api_key}
            onChange={(event) => setForm({ ...form, api_key: event.target.value })}
            placeholder={config.data?.items.find((row) => row.is_default)?.has_key ? "••••••••" : ""}
          />
        </div>
        <div>
          <label className="label" htmlFor="budget">{t("agents.tokens")}</label>
          <input
            id="budget"
            className="input tabular"
            inputMode="numeric"
            value={form.monthly_token_budget}
            onChange={(event) => setForm({ ...form, monthly_token_budget: Number(event.target.value) })}
          />
        </div>
        <div className="flex items-end justify-end">
          <button type="button" className="btn-primary" onClick={() => void save()} disabled={busy}>
            {busy ? <Spinner /> : <Save className="size-4" aria-hidden />}
            {t("common.save")}
          </button>
        </div>
      </div>
      {(config.data?.items ?? []).length > 0 ? (
        <table className="table-dense">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Model</th>
              <th className="text-end">{t("agents.tokens")}</th>
              <th className="text-end">{t("common.total")}</th>
              <th>{t("common.status")}</th>
            </tr>
          </thead>
          <tbody>
            {config.data?.items.map((row) => (
              <tr key={row.id}>
                <td className="text-sm">{row.provider}</td>
                <td className="text-xs" dir="ltr">{row.model}</td>
                <td className="text-end tabular text-xs">{num(row.tokens_used, lang)}</td>
                <td className="text-end tabular text-xs">{num(row.monthly_token_budget, lang)}</td>
                <td>{row.is_default ? <Badge tone="gold">{t("settings.llm")}</Badge> : <span className="text-2xs text-ink-subtle">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}

function Billing({ lang }: { lang: AppLang }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const toast = useToast();
  const usage = useQuery({ queryKey: ["billing-usage"], queryFn: () => api.get<Usage>("/api/v1/billing/usage") });
  const invoices = useQuery({ queryKey: ["invoices"], queryFn: () => api.get<Page<Invoice>>("/api/v1/billing/invoices") });
  const [busy, setBusy] = useState(false);

  const generate = async () => {
    setBusy(true);
    try {
      await api.post("/api/v1/billing/invoices");
      await queryClient.invalidateQueries({ queryKey: ["invoices"] });
      toast.push(t("common.save"), "ok");
    } catch (error) {
      toast.push(errorMessage(error), "danger");
    } finally {
      setBusy(false);
    }
  };

  if (usage.isLoading) return <LoadingBlock rows={4} />;
  if (usage.error) return <ErrorState message={errorMessage(usage.error)} onRetry={() => void usage.refetch()} />;

  const limits = usage.data?.limits ?? {};
  const used = usage.data?.usage ?? {};
  const labels: Record<string, string> = {
    users: t("users.title"),
    rfqs_per_month: t("nav.rfqs"),
    messages_per_month: t("rfqs.messages"),
    llm_tokens_per_month: t("agents.tokens"),
    voice_minutes_per_month: t("agents.voice"),
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={t("settings.usage")}
          subtitle={`${usage.data?.plan ?? ""} · ${usage.data?.subscription?.status ?? ""} · ${money(usage.data?.plan_price, "EGP", lang)}`}
        />
        <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
          {Object.entries(limits).map(([key, limit]) => {
            const quantity = used[key.replace(/_per_month$/, "").replace(/s$/, "")] ?? used[key] ?? 0;
            const pct = limit ? Math.min(100, (Number(quantity) / Number(limit)) * 100) : 0;
            return (
              <div key={key} className="rounded-md border border-line p-3">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-ink-muted">{labels[key] ?? key.replaceAll("_", " ")}</span>
                  <span className="tabular">{num(quantity, lang)} / {num(limit, lang)}</span>
                </div>
                <div className="mt-2">
                  <Progress value={pct} tone={pct > 85 ? "danger" : pct > 60 ? "warn" : "ok"} />
                </div>
              </div>
            );
          })}
        </div>
        <div className="flex items-center justify-between border-t border-line px-4 py-3 text-2xs text-ink-subtle">
          <span>{`${t("settings.billing")}: ${date(usage.data?.subscription?.period_end, lang)} · ${num(usage.data?.subscription?.seats ?? 0, lang)} ${t("users.title")}`}</span>
          <button type="button" className="btn-secondary" onClick={() => void generate()} disabled={busy}>
            {busy ? <Spinner /> : null}
            {t("settings.invoices")}
          </button>
        </div>
      </Card>

      <Card>
        <CardHeader title={t("settings.invoices")} subtitle={`${invoices.data?.items.length ?? 0}`} />
        {invoices.error ? (
          <ErrorState message={errorMessage(invoices.error)} onRetry={() => void invoices.refetch()} />
        ) : (
          <table className="table-dense">
            <thead>
              <tr>
                <th>#</th>
                <th>{t("common.total")}</th>
                <th>{t("common.status")}</th>
                <th>{t("common.date")}</th>
              </tr>
            </thead>
            <tbody>
              {(invoices.data?.items ?? []).map((invoice) => (
                <tr key={invoice.id}>
                  <td className="font-mono text-xs">{invoice.number}</td>
                  <td className="tabular text-sm">{money(invoice.amount, invoice.currency, lang)}</td>
                  <td><StatusChip status={invoice.status} /></td>
                  <td className="text-2xs">{date(invoice.issued_at, lang)}</td>
                </tr>
              ))}
              {(invoices.data?.items ?? []).length === 0 ? (
                <tr>
                  <td colSpan={4}>
                    <EmptyState title={t("common.empty")} description={t("settings.billing")} />
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function Retention() {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const retention = useQuery({ queryKey: ["retention"], queryFn: () => api.get<RetentionPayload>("/api/v1/retention") });
  const [edits, setEdits] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const save = async (entity: string) => {
    setBusy(entity);
    try {
      await api.put("/api/v1/retention", { entity, retain_days: edits[entity] });
      await queryClient.invalidateQueries({ queryKey: ["retention"] });
      toast.push(t("common.save"), "ok");
    } catch (error) {
      toast.push(errorMessage(error), "danger");
    } finally {
      setBusy(null);
    }
  };

  if (retention.isLoading) return <LoadingBlock rows={4} />;
  if (retention.error) return <ErrorState message={errorMessage(retention.error)} onRetry={() => void retention.refetch()} />;

  const configured = new Map((retention.data?.items ?? []).map((row) => [row.entity, row.retain_days]));
  const entities = [...new Set([...RETENTION_ENTITIES, ...configured.keys()])];

  return (
    <Card>
      <CardHeader title={t("settings.retention")} subtitle={t("auth.trust")} />
      <table className="table-dense">
        <thead>
          <tr>
            <th>Entity</th>
            <th className="text-end">{t("dashboard.days")}</th>
            <th className="text-end">{t("common.actions")}</th>
          </tr>
        </thead>
        <tbody>
          {entities.map((entity) => {
            const value = edits[entity] ?? configured.get(entity) ?? retention.data?.defaults.procurement_records_days ?? 365;
            return (
              <tr key={entity}>
                <td className="text-sm">{entity.replaceAll("_", " ")}</td>
                <td className="text-end">
                  <input
                    className="input w-24 py-1 text-end tabular"
                    inputMode="numeric"
                    min={30}
                    max={3650}
                    value={value}
                    onChange={(event) => setEdits((current) => ({ ...current, [entity]: Number(event.target.value) }))}
                    aria-label={`${entity} retention days`}
                  />
                </td>
                <td className="text-end">
                  <button
                    type="button"
                    className="btn-ghost text-2xs"
                    disabled={busy === entity || value < 30 || value > 3650}
                    onClick={() => void save(entity)}
                  >
                    {busy === entity ? <Spinner /> : <Save className="size-3" aria-hidden />}
                    {t("common.save")}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-4 py-3 text-2xs text-ink-subtle">
        <span>{`${t("dashboard.days")}: ${t("settings.retention")} 30–3650`}</span>
        <span>{`Purge after erasure: ${retention.data?.defaults.purge_after_request_days ?? 30} ${t("dashboard.days")}`}</span>
      </div>
    </Card>
  );
}
