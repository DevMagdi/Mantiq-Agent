import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug, RefreshCw, Save, ShieldCheck, ShieldOff } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Card, CardHeader, ErrorState, LoadingBlock, Spinner, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { PERM } from "@/lib/permissions";
import { useAuth } from "@/lib/session";
import type { AppLang } from "@/i18n";
import type {
  IntegrationField,
  IntegrationItem,
  IntegrationKind,
  IntegrationProbe,
  IntegrationStatus,
  IntegrationsPayload,
} from "@/lib/types";

const STATUS_TONE: Record<IntegrationStatus, "ok" | "gold" | "warn" | "danger" | "neutral"> = {
  live: "ok",
  sandbox: "gold",
  not_configured: "warn",
  error: "danger",
  disabled: "neutral",
};

type Pending = "save" | "test" | "toggle";

/**
 * The Integrations screen: one card per provider, driven by the backend catalogue.
 *
 * The catalogue (which fields a provider needs, which of them are secrets, which are required) is
 * served with the data, so this screen never invents a field and never has to be redeployed when a
 * provider gains a credential. Saving writes real credentials through the API; testing performs a
 * real vendor call; disabling hands traffic back to the platform adapter.
 */
export function IntegrationsPanel({ lang }: { lang: AppLang }) {
  const { t } = useTranslation();
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const toast = useToast();
  const canWrite = can(PERM.adminIntegrations);

  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [providers, setProviders] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, IntegrationProbe>>({});
  const [pending, setPending] = useState<Record<string, Pending | undefined>>({});

  const state = useQuery({
    queryKey: ["integrations"],
    queryFn: () => api.get<IntegrationsPayload>("/api/v1/integrations"),
    enabled: can(PERM.adminIntegrations),
  });

  const catalogue = useMemo(() => {
    const map = new Map<string, IntegrationKind>();
    for (const kind of state.data?.catalogue ?? []) map.set(kind.key, kind);
    return map;
  }, [state.data]);

  const refresh = async () => queryClient.invalidateQueries({ queryKey: ["integrations"] });

  const save = useMutation({
    mutationFn: async ({ item, kind }: { item: IntegrationItem; kind: IntegrationKind }) => {
      const spec = kind.providers.find((row) => row.key === (providers[item.kind] ?? item.provider));
      const config: Record<string, string> = {};
      const secrets: Record<string, string> = {};
      for (const field of spec?.fields ?? []) {
        const value = drafts[keyOf(item.kind, field)] ?? (field.secret ? "" : (item.config[field.key] ?? ""));
        if (field.secret) {
          if (value) secrets[field.key] = value;
        } else if (value) {
          config[field.key] = value;
        }
      }
      return api.put<IntegrationItem & { secrets_changed: string[] }>(`/api/v1/integrations/${item.kind}`, {
        provider: spec?.key ?? item.provider,
        config,
        secrets,
        enabled: item.enabled,
      });
    },
    onSuccess: async (saved) => {
      setPending((rows) => ({ ...rows, [saved.kind]: undefined }));
      setResults((rows) => {
        const next = { ...rows };
        delete next[saved.kind];
        return next;
      });
      // Saving clears the secret inputs: the stored value is never echoed back to the browser.
      setDrafts((rows) => {
        const next = { ...rows };
        for (const key of Object.keys(next)) if (key.startsWith(`${saved.kind}.`)) delete next[key];
        return next;
      });
      if (saved.missing.length > 0) {
        toast.push(`${t("integrations.savedIncomplete")}: ${saved.missing.join(", ")}`, "danger");
      } else {
        toast.push(t("integrations.saved"), "ok");
      }
      await refresh();
    },
    onError: (error, variables) => {
      setPending((rows) => ({ ...rows, [variables.item.kind]: undefined }));
      toast.push(errorMessage(error), "danger");
    },
  });

  const test = useMutation({
    mutationFn: (item: IntegrationItem) =>
      api.post<IntegrationProbe>(`/api/v1/integrations/${item.kind}/test`),
    onSuccess: async (probe, item) => {
      setPending((rows) => ({ ...rows, [item.kind]: undefined }));
      setResults((rows) => ({ ...rows, [item.kind]: probe }));
      toast.push(
        probe.ok
          ? `${t("integrations.testOk")} · ${probe.latency_ms} ms`
          : `${t("integrations.testFailed")}: ${probe.detail}`,
        probe.ok ? "ok" : "danger",
      );
      await refresh();
    },
    onError: (error, item) => {
      setPending((rows) => ({ ...rows, [item.kind]: undefined }));
      toast.push(errorMessage(error), "danger");
    },
  });

  const toggle = useMutation({
    mutationFn: (item: IntegrationItem) =>
      api.patch<{ enabled: boolean }>(`/api/v1/integrations/${item.kind}`, { enabled: !item.enabled }),
    onSuccess: async (_result, item) => {
      setPending((rows) => ({ ...rows, [item.kind]: undefined }));
      toast.push(item.enabled ? t("integrations.disabledToast") : t("integrations.enabledToast"), "ok");
      await refresh();
    },
    onError: (error, item) => {
      setPending((rows) => ({ ...rows, [item.kind]: undefined }));
      toast.push(errorMessage(error), "danger");
    },
  });

  if (!can(PERM.adminIntegrations)) return <p className="text-sm text-ink-subtle">{t("access.cardBody")}</p>;
  if (state.isLoading) return <LoadingBlock rows={6} />;
  if (state.error) return <ErrorState message={errorMessage(state.error)} onRetry={() => void state.refetch()} />;

  const items = state.data?.items ?? [];
  const editable = items.filter((item) => catalogue.get(item.kind)?.tenant_managed);
  const readOnly = items.filter((item) => !catalogue.get(item.kind)?.tenant_managed);

  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-muted">{t("integrations.hint")}</p>

      <div className="grid gap-4 2xl:grid-cols-2">
        {editable.map((item) => {
          const kind = catalogue.get(item.kind);
          if (!kind) return null;
          return (
            <IntegrationCard
              key={item.kind}
              item={item}
              kind={kind}
              lang={lang}
              canWrite={canWrite}
              pending={pending[item.kind]}
              result={results[item.kind]}
              provider={providers[item.kind] ?? item.provider}
              onProvider={(value) => setProviders((rows) => ({ ...rows, [item.kind]: value }))}
              draftOf={(field) => drafts[keyOf(item.kind, field)] ?? (field.secret ? "" : (item.config[field.key] ?? ""))}
              onField={(field, value) => setDrafts((rows) => ({ ...rows, [keyOf(item.kind, field)]: value }))}
              onSave={() => {
                setPending((rows) => ({ ...rows, [item.kind]: "save" }));
                save.mutate({ item, kind });
              }}
              onTest={() => {
                setPending((rows) => ({ ...rows, [item.kind]: "test" }));
                test.mutate(item);
              }}
              onToggle={() => {
                setPending((rows) => ({ ...rows, [item.kind]: "toggle" }));
                toggle.mutate(item);
              }}
            />
          );
        })}
      </div>

      {readOnly.length > 0 ? (
        <Card>
          <CardHeader title={t("integrations.platformOnly")} subtitle={t("integrations.platformOnlyHint")} />
          <ul className="divide-y divide-line/60">
            {readOnly.map((item) => (
              <li key={item.kind} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
                <div className="flex items-center gap-2">
                  <Plug className="size-4 text-gold" aria-hidden />
                  <span className="text-sm text-ink">{kindLabel(catalogue.get(item.kind), lang, item.kind)}</span>
                  <span className="text-2xs text-ink-subtle" dir="ltr">{item.platform_provider}</span>
                </div>
                <StatusBadge status={item.status} />
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

function IntegrationCard({
  item,
  kind,
  lang,
  canWrite,
  pending,
  result,
  provider,
  onProvider,
  draftOf,
  onField,
  onSave,
  onTest,
  onToggle,
}: {
  item: IntegrationItem;
  kind: IntegrationKind;
  lang: AppLang;
  canWrite: boolean;
  pending?: Pending;
  result?: IntegrationProbe;
  provider: string;
  onProvider: (value: string) => void;
  draftOf: (field: IntegrationField) => string;
  onField: (field: IntegrationField, value: string) => void;
  onSave: () => void;
  onTest: () => void;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const spec = kind.providers.find((row) => row.key === provider) ?? kind.providers[0];
  const busy = Boolean(pending);

  return (
    <Card>
      <CardHeader
        title={kindLabel(kind, lang, item.kind)}
        subtitle={lang === "ar" ? kind.description_ar : kind.description_en}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={item.status} />
            <span className="text-2xs text-ink-subtle">
              {item.provider_source === "tenant"
                ? t("integrations.sourceTenant")
                : t("integrations.sourcePlatform")}
            </span>
          </div>
        }
      />
      <div className="space-y-3 p-4">
        {kind.providers.length > 1 ? (
          <div>
            <label className="label" htmlFor={`integration-provider-${item.kind}`}>
              {t("integrations.provider")}
            </label>
            <select
              id={`integration-provider-${item.kind}`}
              className="input"
              value={provider}
              disabled={!canWrite || busy}
              onChange={(event) => onProvider(event.target.value)}
            >
              {kind.providers.map((row) => (
                <option key={row.key} value={row.key}>{row.label}</option>
              ))}
            </select>
          </div>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2">
          {(spec?.fields ?? []).map((field) => {
            const id = `integration-${item.kind}-${field.key}`;
            const saved = field.secret ? item.masked[field.key] : "";
            return (
              <div key={field.key} className={field.kind === "url" ? "sm:col-span-2" : undefined}>
                <label className="label" htmlFor={id}>
                  {lang === "ar" ? field.label_ar : field.label_en}
                  {field.required ? <span aria-hidden className="text-danger"> *</span> : null}
                </label>
                <input
                  id={id}
                  className="input"
                  type={field.secret ? "password" : field.kind === "number" ? "number" : "text"}
                  dir={field.kind === "url" || field.secret ? "ltr" : undefined}
                  autoComplete={field.secret ? "new-password" : "off"}
                  placeholder={saved || field.placeholder}
                  value={draftOf(field)}
                  disabled={!canWrite || busy}
                  onChange={(event) => onField(field, event.target.value)}
                  aria-describedby={`${id}-help`}
                />
                <p id={`${id}-help`} className="mt-1 text-2xs text-ink-subtle">
                  {field.secret && saved ? `${t("integrations.savedMask")} ${saved}` : lang === "ar" ? field.help_ar : field.help_en}
                </p>
              </div>
            );
          })}
        </div>

        {item.missing.length > 0 ? (
          <p className="rounded border border-danger/40 bg-danger/10 px-2 py-1.5 text-2xs text-danger">
            {t("integrations.missing")}: <span dir="ltr">{item.missing.join(", ")}</span>
          </p>
        ) : null}

        {result || item.last_error ? (
          <p
            className={
              (result ? result.ok : item.last_test_ok === true)
                ? "rounded border border-ok/40 bg-ok/10 px-2 py-1.5 text-2xs text-ok"
                : "rounded border border-danger/40 bg-danger/10 px-2 py-1.5 text-2xs text-danger"
            }
            dir="auto"
          >
            {result ? result.detail : item.last_error}
            {result ? ` · ${result.latency_ms} ms` : ""}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-primary" onClick={onSave} disabled={!canWrite || busy}>
            {pending === "save" ? <Spinner /> : <Save className="size-4" aria-hidden />}
            {t("common.save")}
          </button>
          <button type="button" className="btn-secondary" onClick={onTest} disabled={!canWrite || busy}>
            {pending === "test" ? <Spinner /> : <RefreshCw className="size-4" aria-hidden />}
            {t("integrations.test")}
          </button>
          <button type="button" className="btn-ghost" onClick={onToggle} disabled={!canWrite || busy}>
            {pending === "toggle" ? (
              <Spinner />
            ) : item.enabled ? (
              <ShieldOff className="size-4" aria-hidden />
            ) : (
              <ShieldCheck className="size-4" aria-hidden />
            )}
            {item.enabled ? t("integrations.disable") : t("integrations.enable")}
          </button>
        </div>
      </div>
    </Card>
  );
}

function StatusBadge({ status }: { status: IntegrationStatus }) {
  const { t } = useTranslation();
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{t(`integrations.status.${status}`)}</Badge>;
}

function kindLabel(kind: IntegrationKind | undefined, lang: AppLang, fallback: string): string {
  if (!kind) return fallback;
  return lang === "ar" ? kind.label_ar : kind.label_en;
}

function keyOf(kind: string, field: IntegrationField): string {
  return `${kind}.${field.key}`;
}
