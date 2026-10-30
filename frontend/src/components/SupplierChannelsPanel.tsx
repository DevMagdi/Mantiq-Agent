import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, ShieldOff } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, ErrorState, LoadingBlock, Spinner, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { PERM } from "@/lib/permissions";
import { useAuth } from "@/lib/session";
import type { AppLang } from "@/i18n";
import type { SupplierChannels } from "@/lib/types";

const CHANNELS = ["whatsapp", "voice", "email", "web_form"] as const;

/** Channel preference and consent for one supplier — the same rules the backend enforces. */
export function SupplierChannelsPanel({ supplierId }: { supplierId: string }) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const canWrite = can(PERM.channelWrite);

  const [preferred, setPreferred] = useState("");
  const [secondary, setSecondary] = useState<string[]>([]);
  const [language, setLanguage] = useState("ar");

  const state = useQuery({
    queryKey: ["supplier-channels", supplierId],
    queryFn: () => api.get<SupplierChannels>(`/api/v1/suppliers/${supplierId}/channels`),
    enabled: can(PERM.channelRead),
  });

  useEffect(() => {
    if (!state.data) return;
    setPreferred(state.data.preferred_channel);
    setSecondary(state.data.secondary_channels ?? []);
    setLanguage(state.data.preferred_language || "ar");
  }, [state.data]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["supplier-channels", supplierId] });
  };

  const save = useMutation({
    mutationFn: () =>
      api.patch(`/api/v1/suppliers/${supplierId}/channels`, {
        preferred_channel: preferred,
        secondary_channels: secondary,
        preferred_language: language,
      }),
    onSuccess: async () => {
      toast.push(t("channels.prefsSaved"), "ok");
      await refresh();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const consent = useMutation({
    mutationFn: (payload: { channel: string; status: "opted_in" | "opted_out" }) =>
      api.post(`/api/v1/suppliers/${supplierId}/opt-in`, {
        channel: payload.channel,
        status: payload.status,
        evidence: t("channels.consentEvidence"),
      }),
    onSuccess: async () => {
      toast.push(t("channels.consentSaved"), "ok");
      await refresh();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  if (!can(PERM.channelRead)) {
    return <p className="text-xs text-ink-subtle">{t("channels.noAccess")}</p>;
  }
  if (state.isLoading) return <LoadingBlock rows={4} />;
  if (state.error) return <ErrorState message={errorMessage(state.error)} onRetry={() => void state.refetch()} />;
  if (!state.data) return null;

  const toggleSecondary = (channel: string) => {
    setSecondary((current) =>
      current.includes(channel) ? current.filter((row) => row !== channel) : [...current, channel],
    );
  };

  return (
    <div className="space-y-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="preferred-channel">
            {t("channels.preferred")}
          </label>
          <select
            id="preferred-channel"
            className="input"
            value={preferred}
            disabled={!canWrite}
            onChange={(event) => setPreferred(event.target.value)}
          >
            {CHANNELS.map((key) => (
              <option key={key} value={key}>
                {t(`channels.names.${key}`)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="preferred-language">
            {t("common.language")}
          </label>
          <select
            id="preferred-language"
            className="input"
            value={language}
            disabled={!canWrite}
            onChange={(event) => setLanguage(event.target.value)}
          >
            <option value="ar">العربية</option>
            <option value="en">English</option>
          </select>
        </div>
      </div>

      <div>
        <p className="label">{t("channels.secondary")}</p>
        <div className="flex flex-wrap gap-2">
          {CHANNELS.filter((key) => key !== preferred).map((key) => (
            <label key={key} className="flex items-center gap-1.5 text-xs text-ink-muted">
              <input
                type="checkbox"
                checked={secondary.includes(key)}
                disabled={!canWrite}
                onChange={() => toggleSecondary(key)}
              />
              {t(`channels.names.${key}`)}
            </label>
          ))}
        </div>
      </div>

      {canWrite ? (
        <button type="button" className="btn-secondary" disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? <Spinner /> : null} {t("common.save")}
        </button>
      ) : null}

      <div className="space-y-1.5">
        <p className="label">{t("channels.reachability")}</p>
        {state.data.channels.map((row) => (
          <div key={row.channel} className="flex flex-wrap items-center justify-between gap-2 rounded border border-line px-2 py-1.5">
            <div className="min-w-0">
              <p className="text-xs text-ink">{lang === "ar" ? row.label_ar : row.label}</p>
              <p className="text-2xs text-ink-subtle">
                {row.address_hint || "—"}
                {row.reasons.length > 0 ? ` · ${row.reasons.join(", ")}` : ""}
              </p>
            </div>
            <div className="flex items-center gap-1">
              <Badge tone={row.available ? "ok" : "warn"}>
                {row.opted_in ? t("channels.optedIn") : t("channels.optedOut")}
              </Badge>
              {canWrite && row.channel !== "email" ? (
                <button
                  type="button"
                  className="btn-ghost text-2xs"
                  disabled={consent.isPending}
                  onClick={() =>
                    consent.mutate({ channel: row.channel, status: row.opted_in ? "opted_out" : "opted_in" })
                  }
                >
                  {row.opted_in ? <ShieldOff className="size-3" aria-hidden /> : <ShieldCheck className="size-3" aria-hidden />}
                  {row.opted_in ? t("channels.optOut") : t("channels.optIn")}
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
