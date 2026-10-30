import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownLeft, ArrowUpRight, Inbox, Link2, Mail, MessageCircle, Phone, Search, Send } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { PageHeading } from "@/components/Shell";
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingBlock,
  Spinner,
  StatusChip,
  useToast,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { relative } from "@/lib/format";
import { PERM } from "@/lib/permissions";
import { useAuth } from "@/lib/session";
import type { AppLang } from "@/i18n";
import type { InboxList, InboxThreadDetail, TemplateLibrary } from "@/lib/types";

const CHANNEL_ICON: Record<string, typeof Mail> = {
  whatsapp: MessageCircle,
  email: Mail,
  voice: Phone,
  web_form: Link2,
};

/** The channel chip is the single most important piece of information on this screen. */
function ChannelChip({ channel, label, labelAr, lang }: { channel: string; label?: string; labelAr?: string; lang: AppLang }) {
  const Icon = CHANNEL_ICON[channel] ?? Link2;
  const name = (lang === "ar" && labelAr ? labelAr : label) ?? channel;
  return (
    <span className="chip border-gold/40 bg-gold/10 text-[color:var(--gold-300)]">
      <Icon className="size-3" aria-hidden /> {name}
    </span>
  );
}

export default function InboxPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [channel, setChannel] = useState("");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState("");
  const [replyChannel, setReplyChannel] = useState("");
  const [templateId, setTemplateId] = useState("");

  const canReply = can(PERM.inboxReply);

  const list = useQuery({
    queryKey: ["inbox", channel, status, query],
    queryFn: () =>
      api.get<InboxList>(
        `/api/v1/inbox?limit=100${channel ? `&channel=${channel}` : ""}${status ? `&status=${status}` : ""}${
          query.trim() ? `&q=${encodeURIComponent(query.trim())}` : ""
        }`,
      ),
    refetchInterval: 30_000,
  });

  const threads = list.data?.items ?? [];
  const activeId = selected ?? threads[0]?.thread_id ?? null;

  const detail = useQuery({
    queryKey: ["inbox-thread", activeId],
    queryFn: () => api.get<InboxThreadDetail>(`/api/v1/inbox/threads/${activeId}`),
    enabled: Boolean(activeId),
  });

  const library = useQuery({
    queryKey: ["template-library"],
    queryFn: () => api.get<TemplateLibrary>("/api/v1/templates/library"),
    enabled: canReply,
  });

  const templates = useMemo(() => {
    const target = (replyChannel || detail.data?.reply_channel || "whatsapp").replace("web_form", "email");
    const rows = library.data?.items?.[target] ?? [];
    return rows.filter((row) => row.locale === (lang === "ar" ? "ar" : "en"));
  }, [library.data, replyChannel, detail.data?.reply_channel, lang]);

  const send = useMutation({
    mutationFn: (payload: { body: string; channel?: string }) =>
      api.post(`/api/v1/inbox/threads/${activeId}/reply`, {
        body: payload.body,
        channel: payload.channel || undefined,
        locale: lang,
      }),
    onSuccess: async () => {
      setDraft("");
      setTemplateId("");
      toast.push(t("inbox.sent"), "ok");
      await queryClient.invalidateQueries({ queryKey: ["inbox"] });
      await queryClient.invalidateQueries({ queryKey: ["inbox-thread"] });
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const submit = () => {
    const body = draft.trim();
    if (!body) return;
    send.mutate({ body, channel: replyChannel || undefined });
  };

  const active = detail.data;

  return (
    <div className="space-y-4">
      <PageHeading
        eyebrow={t("inbox.eyebrow")}
        title={t("inbox.title")}
        subtitle={t("inbox.subtitle")}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute inset-y-0 start-2 my-auto size-4 text-ink-subtle" aria-hidden />
              <input
                className="input ps-8"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("inbox.searchPlaceholder")}
                aria-label={t("inbox.searchPlaceholder")}
              />
            </div>
            <select className="input" value={channel} onChange={(event) => setChannel(event.target.value)} aria-label={t("inbox.channelFilter")}>
              <option value="">{t("inbox.allChannels")}</option>
              {["whatsapp", "voice", "email", "web_form"].map((key) => (
                <option key={key} value={key}>
                  {t(`channels.names.${key}`)}
                </option>
              ))}
            </select>
            <select className="input" value={status} onChange={(event) => setStatus(event.target.value)} aria-label={t("inbox.statusFilter")}>
              <option value="">{t("common.all")}</option>
              <option value="awaiting_reply">{t("inbox.awaiting")}</option>
              <option value="replied">{t("channels.status.replied")}</option>
              <option value="no_response">{t("channels.status.no_response")}</option>
            </select>
          </div>
        }
      />

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-4 xl:col-span-3">
          <CardHeader
            title={t("inbox.conversations")}
            subtitle={list.data ? t("inbox.count", { count: list.data.total }) : undefined}
            action={list.data && list.data.awaiting_reply > 0 ? <Badge tone="warn">{t("inbox.awaitingCount", { count: list.data.awaiting_reply })}</Badge> : undefined}
          />
          {list.isLoading ? (
            <LoadingBlock rows={8} />
          ) : list.error ? (
            <ErrorState message={errorMessage(list.error)} onRetry={() => void list.refetch()} />
          ) : threads.length === 0 ? (
            <EmptyState icon={<Inbox className="size-5" aria-hidden />} title={t("inbox.empty")} description={t("inbox.emptyHint")} />
          ) : (
            <ul className="max-h-[calc(100vh-16rem)] divide-y divide-line/60 overflow-y-auto">
              {threads.map((thread) => {
                const isActive = thread.thread_id === activeId;
                return (
                  <li key={thread.thread_id}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelected(thread.thread_id);
                        setReplyChannel("");
                        setDraft("");
                      }}
                      aria-current={isActive ? "true" : undefined}
                      className={`w-full px-4 py-3 text-start transition-colors ${
                        isActive ? "bg-surface-raised" : "hover:bg-surface-raised/60"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-sm text-ink">
                          {lang === "ar" && thread.supplier_name_ar ? thread.supplier_name_ar : thread.supplier_name}
                        </p>
                        <span className="shrink-0 text-2xs text-ink-subtle">{relative(thread.last_message_at, lang)}</span>
                      </div>
                      <p className="mt-0.5 truncate text-2xs text-ink-subtle">
                        {thread.rfq_code} · {thread.message_count} {t("inbox.messages")}
                      </p>
                      <p className="mt-1 line-clamp-2 text-xs text-ink-muted">{thread.last_message.body}</p>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1">
                        {thread.channels_used.map((key) => (
                          <ChannelChip key={key} channel={key} lang={lang} label={t(`channels.names.${key}`)} />
                        ))}
                        {thread.awaiting_reply ? <Badge tone="warn">{t("inbox.awaiting")}</Badge> : null}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card className="lg:col-span-8 xl:col-span-9">
          {!activeId ? (
            <EmptyState title={t("inbox.pickConversation")} />
          ) : detail.isLoading ? (
            <LoadingBlock rows={10} />
          ) : detail.error ? (
            <ErrorState message={errorMessage(detail.error)} onRetry={() => void detail.refetch()} />
          ) : active ? (
            <div className="flex h-[calc(100vh-16rem)] flex-col">
              <div className="border-b border-line px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-ink">
                      {lang === "ar" && active.supplier_name_ar ? active.supplier_name_ar : active.supplier_name}
                    </p>
                    <p className="text-2xs text-ink-subtle">
                      <Link className="text-gold hover:underline" to={`/rfqs/${active.rfq_id}`}>
                        {active.rfq_code}
                      </Link>{" "}
                      · {active.rfq_title}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-1">
                    <StatusChip status={active.comm_status} />
                    {active.channels_used.map((key) => (
                      <ChannelChip key={key} channel={key} lang={lang} label={t(`channels.names.${key}`)} />
                    ))}
                  </div>
                </div>
              </div>

              <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
                {active.messages.length === 0 ? (
                  <EmptyState title={t("inbox.noMessages")} />
                ) : (
                  active.messages.map((message) => {
                    const inbound = message.direction === "inbound";
                    return (
                      <article
                        key={message.id}
                        className={`max-w-[85%] rounded-lg border px-3 py-2 ${
                          inbound
                            ? "border-line bg-surface-raised/70"
                            : "ms-auto border-gold/30 bg-gold/5"
                        }`}
                      >
                        <header className="mb-1 flex flex-wrap items-center gap-1.5 text-2xs text-ink-subtle">
                          {inbound ? <ArrowDownLeft className="size-3" aria-hidden /> : <ArrowUpRight className="size-3" aria-hidden />}
                          <ChannelChip channel={message.channel} lang={lang} label={message.channel_label} labelAr={message.channel_label_ar} />
                          <span>{relative(message.created_at, lang)}</span>
                          {!inbound && message.delivery_status ? <span>· {t(`channels.delivery.${message.delivery_status}`, message.delivery_status)}</span> : null}
                          {message.template_code ? <span>· {message.template_code}</span> : null}
                        </header>
                        {message.subject ? <p className="text-xs font-medium text-ink">{message.subject}</p> : null}
                        <p className="whitespace-pre-wrap text-sm text-ink">{message.body}</p>
                      </article>
                    );
                  })
                )}
              </div>

              <div className="border-t border-line px-4 py-3">
                {!canReply ? (
                  <p className="text-xs text-ink-subtle">{t("inbox.readOnly")}</p>
                ) : (
                  <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <label className="text-2xs text-ink-muted" htmlFor="inbox-channel">
                        {t("inbox.replyOn")}
                      </label>
                      <select
                        id="inbox-channel"
                        className="input py-1"
                        value={replyChannel || active.reply_channel}
                        onChange={(event) => {
                          setReplyChannel(event.target.value);
                          setTemplateId("");
                        }}
                      >
                        {active.channels_used.map((key) => (
                          <option key={key} value={key}>
                            {t(`channels.names.${key}`)}
                          </option>
                        ))}
                      </select>
                      {templates.length > 0 ? (
                        <select
                          className="input py-1"
                          value={templateId}
                          onChange={(event) => {
                            const chosen = templates.find((row) => row.id === event.target.value);
                            setTemplateId(event.target.value);
                            if (chosen) setDraft(chosen.body);
                          }}
                          aria-label={t("templates.title")}
                        >
                          <option value="">{t("inbox.insertTemplate")}</option>
                          {templates.map((row) => (
                            <option key={row.id} value={row.id}>
                              {row.name || row.code}
                            </option>
                          ))}
                        </select>
                      ) : null}
                    </div>
                    <div className="flex items-end gap-2">
                      <textarea
                        className="input min-h-20 flex-1 resize-y"
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        placeholder={t("inbox.replyPlaceholder")}
                        aria-label={t("inbox.replyPlaceholder")}
                      />
                      <button type="button" className="btn-primary" disabled={!draft.trim() || send.isPending} onClick={submit}>
                        {send.isPending ? <Spinner /> : <Send className="size-4" aria-hidden />} {t("inbox.send")}
                      </button>
                    </div>
                    {active.resolved_channel && !active.resolved_channel.available ? (
                      <p className="text-2xs text-[color:var(--warn-ink)]">
                        {t("channels.unavailableHint", { reasons: active.resolved_channel.reasons.join(", ") })}
                      </p>
                    ) : null}
                  </div>
                )}
              </div>
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
}
