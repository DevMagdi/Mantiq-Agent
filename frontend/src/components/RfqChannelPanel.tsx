import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageCircle, Send, Shuffle } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Card, CardHeader, Drawer, EmptyState, ErrorState, LoadingBlock, Spinner, StatusChip, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { relative } from "@/lib/format";
import { PERM } from "@/lib/permissions";
import { useAuth } from "@/lib/session";
import type { AppLang } from "@/i18n";
import type { RfqChannelRow, RfqChannels, TemplateLibrary } from "@/lib/types";

const CHANNELS = ["", "whatsapp", "voice", "email", "web_form"] as const;

/**
 * Where each supplier actually is: the resolved channel, why it was chosen, what happened to the
 * message, and the two actions a buyer needs — send on WhatsApp, or move the conversation.
 */
export function RfqChannelPanel({ rfqId, onSent }: { rfqId: string; onSent?: () => void }) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const queryClient = useQueryClient();
  const { can } = useAuth();
  const [target, setTarget] = useState<RfqChannelRow | null>(null);
  const [mode, setMode] = useState<"whatsapp" | "switch">("whatsapp");
  const [body, setBody] = useState("");
  const [templateCode, setTemplateCode] = useState("");
  const [quoteRequest, setQuoteRequest] = useState(false);

  const canWrite = can(PERM.channelWrite);
  const canSend = can(PERM.rfqSend);

  const channels = useQuery({
    queryKey: ["rfq-channels", rfqId],
    queryFn: () => api.get<RfqChannels>(`/api/v1/rfqs/${rfqId}/channels`),
  });

  const library = useQuery({
    queryKey: ["template-library"],
    queryFn: () => api.get<TemplateLibrary>("/api/v1/templates/library"),
    enabled: Boolean(target),
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["rfq-channels", rfqId] });
    onSent?.();
  };

  const override = useMutation({
    mutationFn: (payload: { supplier_id: string; channel: string | null }) =>
      api.put(`/api/v1/rfqs/${rfqId}/suppliers/${payload.supplier_id}/channel`, {
        channel: payload.channel,
        reason: t("channels.overrideReason"),
      }),
    onSuccess: async () => {
      toast.push(t("channels.overrideSaved"), "ok");
      await refresh();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const sendWhatsApp = useMutation({
    mutationFn: (supplierId: string) =>
      api.post<{ ok: boolean; error?: string; codes?: string[] }>(`/api/v1/rfqs/${rfqId}/whatsapp`, {
        supplier_id: supplierId,
        body: body.trim(),
        template_code: templateCode,
        locale: lang,
        kind: templateCode && !body.trim() ? "template" : "text",
        quote_request: quoteRequest,
      }),
    onSuccess: async (result) => {
      if (result.ok) {
        toast.push(t("channels.sent"), "ok");
        setTarget(null);
        setBody("");
        setTemplateCode("");
        setQuoteRequest(false);
      } else {
        toast.push(result.error ?? t("channels.sendFailed"), "danger");
      }
      await refresh();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const switchChannel = useMutation({
    mutationFn: (payload: { supplier_id: string; channel: string }) =>
      api.post(`/api/v1/rfqs/${rfqId}/suppliers/${payload.supplier_id}/switch-channel`, {
        channel: payload.channel,
        reason: t("channels.switchReason"),
      }),
    onSuccess: async () => {
      toast.push(t("channels.switched"), "ok");
      setTarget(null);
      await refresh();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const templates = (library.data?.items.whatsapp ?? []).filter((row) => row.locale === lang);

  return (
    <Card>
      <CardHeader
        title={t("channels.rfqTitle")}
        subtitle={channels.data ? t("channels.rfqSubtitle", { count: channels.data.items.length }) : undefined}
        action={
          channels.data?.mixed_channels ? <Badge tone="gold">{t("channels.mixed")}</Badge> : undefined
        }
      />
      {channels.isLoading ? (
        <LoadingBlock rows={4} />
      ) : channels.error ? (
        <ErrorState message={errorMessage(channels.error)} onRetry={() => void channels.refetch()} />
      ) : (channels.data?.items ?? []).length === 0 ? (
        <EmptyState title={t("channels.empty")} description={t("channels.emptyHint")} />
      ) : (
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>{t("common.supplier")}</th>
                <th>{t("channels.channel")}</th>
                <th>{t("channels.source")}</th>
                <th>{t("channels.commStatus")}</th>
                <th>{t("channels.lastActivity")}</th>
                <th>{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {(channels.data?.items ?? []).map((row) => {
                const name = lang === "ar" && row.supplier_name_ar ? row.supplier_name_ar : row.supplier_name;
                return (
                  <tr key={row.supplier_id}>
                    <td className="max-w-56">
                      <p className="truncate text-sm text-ink">{name}</p>
                      {row.reasons.length > 0 ? <p className="text-2xs text-[color:var(--warn-ink)]">{row.reasons.join(" · ")}</p> : null}
                    </td>
                    <td>
                      <div className="flex items-center gap-1">
                        <Badge tone={row.channel === "whatsapp" ? "gold" : row.available ? "info" : "danger"}>
                          {lang === "ar" ? row.channel_label_ar : row.channel_label}
                        </Badge>
                      </div>
                      {canWrite ? (
                        <select
                          className="input mt-1 py-0.5 text-2xs"
                          value={row.channel_override ?? ""}
                          onChange={(event) =>
                            override.mutate({ supplier_id: row.supplier_id, channel: event.target.value || null })
                          }
                          aria-label={t("channels.override")}
                        >
                          {CHANNELS.map((key) => (
                            <option key={key || "auto"} value={key}>
                              {key ? t(`channels.names.${key}`) : t("channels.auto")}
                            </option>
                          ))}
                        </select>
                      ) : null}
                    </td>
                    <td className="text-2xs text-ink-subtle">{lang === "ar" ? row.source_label_ar : row.source_label}</td>
                    <td>
                      <StatusChip status={row.comm_status} label={lang === "ar" ? row.comm_status_label_ar : row.comm_status_label} />
                      <p className="mt-0.5 text-2xs text-ink-subtle">
                        {`${row.replies} ${t("channels.replies")}`}
                        {row.delivered_at ? ` · ${t("channels.delivered")}` : ""}
                        {row.read_at ? ` · ${t("channels.read")}` : ""}
                      </p>
                    </td>
                    <td className="text-2xs text-ink-subtle">{row.last_message_at ? relative(row.last_message_at, lang) : "—"}</td>
                    <td>
                      <div className="flex flex-wrap items-center gap-1">
                        {canSend ? (
                          <button
                            type="button"
                            className="btn-ghost text-2xs"
                            onClick={() => {
                              setTarget(row);
                              setMode("whatsapp");
                              setBody("");
                              setTemplateCode("");
                              setQuoteRequest(false);
                            }}
                            disabled={!row.available}
                            title={row.available ? undefined : row.reasons.join(" · ")}
                          >
                            <MessageCircle className="size-3" aria-hidden /> {t("channels.sendWhatsapp")}
                          </button>
                        ) : null}
                        {canWrite ? (
                          <button
                            type="button"
                            className="btn-ghost text-2xs"
                            onClick={() => {
                              setTarget(row);
                              setMode("switch");
                            }}
                          >
                            <Shuffle className="size-3" aria-hidden /> {t("channels.switch")}
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Drawer
        open={Boolean(target)}
        onClose={() => setTarget(null)}
        title={mode === "whatsapp" ? t("channels.sendWhatsapp") : t("channels.switch")}
        description={target ? (lang === "ar" && target.supplier_name_ar ? target.supplier_name_ar : target.supplier_name) : undefined}
        footer={
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => setTarget(null)}>
              {t("common.cancel")}
            </button>
            {mode === "whatsapp" ? (
              <button
                type="button"
                className="btn-primary"
                disabled={sendWhatsApp.isPending || (!body.trim() && !templateCode && !quoteRequest)}
                onClick={() => target && sendWhatsApp.mutate(target.supplier_id)}
              >
                {sendWhatsApp.isPending ? <Spinner /> : <Send className="size-4" aria-hidden />} {t("channels.send")}
              </button>
            ) : (
              <button
                type="button"
                className="btn-primary"
                disabled={switchChannel.isPending}
                onClick={() => {
                  if (!target) return;
                  const next = target.channel === "whatsapp" ? "voice" : "whatsapp";
                  switchChannel.mutate({ supplier_id: target.supplier_id, channel: next });
                }}
              >
                {switchChannel.isPending ? <Spinner /> : <Shuffle className="size-4" aria-hidden />}
                {target?.channel === "whatsapp" ? t("channels.switchToVoice") : t("channels.switchToWhatsapp")}
              </button>
            )}
          </div>
        }
      >
        {mode === "whatsapp" ? (
          <div className="space-y-3">
            <div>
              <label className="label" htmlFor="wa-template">
                {t("channels.template")}
              </label>
              <select
                id="wa-template"
                className="input"
                value={templateCode}
                onChange={(event) => {
                  const code = event.target.value;
                  setTemplateCode(code);
                  const chosen = templates.find((row) => row.code === code);
                  if (chosen && !body.trim()) setBody(chosen.body);
                }}
              >
                <option value="">{t("channels.noTemplate")}</option>
                {templates.map((row) => (
                  <option key={row.id} value={row.code}>
                    {row.name || row.code}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="wa-body">
                {t("channels.message")}
              </label>
              <textarea
                id="wa-body"
                className="input min-h-28"
                value={body}
                onChange={(event) => setBody(event.target.value)}
                placeholder={t("channels.messagePlaceholder")}
                dir={lang === "ar" ? "rtl" : "ltr"}
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-ink-muted">
              <input type="checkbox" checked={quoteRequest} onChange={(event) => setQuoteRequest(event.target.checked)} />
              {t("channels.quoteRequest")}
            </label>
            <p className="text-2xs text-ink-subtle">{t("channels.consentNote")}</p>
          </div>
        ) : (
          <p className="text-sm text-ink-muted">{t("channels.switchHint")}</p>
        )}
      </Drawer>
    </Card>
  );
}
