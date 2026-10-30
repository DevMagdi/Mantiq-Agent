import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Mic, Send, ShieldAlert } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { PageHeading } from "@/components/Shell";
import { Badge, Card, CardHeader, EmptyState, ErrorState, LoadingBlock, Spinner, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { relative } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { AssistantAnswer, AssistantSessionDetail, AssistantSessionRow } from "@/lib/types";

type Turn = { role: "user" | "assistant"; text: string; answer?: AssistantAnswer };

export default function AssistantPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const toast = useToast();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [openSession, setOpenSession] = useState<string | null>(null);

  const sessions = useQuery({
    queryKey: ["assistant-sessions"],
    queryFn: () => api.get<{ items: AssistantSessionRow[] }>("/api/v1/assistant/sessions"),
  });

  const detail = useQuery({
    queryKey: ["assistant-session", openSession],
    queryFn: () => api.get<AssistantSessionDetail>(`/api/v1/assistant/sessions/${openSession}`),
    enabled: Boolean(openSession),
  });

  const ask = useMutation({
    mutationFn: (payload: { text: string; channel?: "text" | "voice" }) =>
      api.post<AssistantAnswer>("/api/v1/assistant/ask", {
        text: payload.text,
        channel: payload.channel ?? "text",
        locale: lang,
        session_id: sessionId,
      }),
    onSuccess: (answer, variables) => {
      setSessionId(answer.session_id ?? sessionId);
      setTurns((current) => [
        ...current,
        { role: "user", text: variables.text },
        { role: "assistant", text: lang === "ar" ? answer.answer_ar : answer.answer_en, answer },
      ]);
      setQuestion("");
      void queryClient.invalidateQueries({ queryKey: ["assistant-sessions"] });
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const transcribe = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.upload<{ text: string; answer?: AssistantAnswer }>(
        `/api/v1/assistant/transcribe?ask=true&locale=${lang}`,
        form,
      );
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const submit = () => {
    const text = question.trim();
    if (!text) return;
    ask.mutate({ text });
  };

  return (
    <div className="space-y-4">
      <PageHeading eyebrow={t("assistant.eyebrow")} title={t("assistant.title")} subtitle={t("assistant.subtitle")} />

      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="flex flex-col lg:col-span-8">
          <CardHeader title={t("assistant.chat")} subtitle={t("assistant.readOnlyHint")} />
          <div className="flex-1 space-y-3 px-4 py-4">
            {turns.length === 0 ? (
              <EmptyState icon={<Bot className="size-5" aria-hidden />} title={t("assistant.empty")} description={t("assistant.emptyHint")} />
            ) : (
              turns.map((turn, index) =>
                turn.role === "user" ? (
                  <p key={index} className="ms-auto max-w-[85%] rounded-lg border border-gold/30 bg-gold/5 px-3 py-2 text-sm text-ink">
                    {turn.text}
                  </p>
                ) : (
                  <article key={index} className="max-w-[90%] rounded-lg border border-line bg-surface-raised/70 px-3 py-2">
                    <p className="whitespace-pre-wrap text-sm text-ink">{turn.text}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1">
                      {turn.answer?.intent ? <Badge tone="info">{t(`assistant.intents.${turn.answer.intent}`, turn.answer.intent)}</Badge> : null}
                      {turn.answer?.blocked_code ? <Badge tone="danger">{turn.answer.blocked_code}</Badge> : null}
                      {turn.answer?.citations?.map((citation) => (
                        <Link key={citation.id} to={citation.link} className="chip border-gold/40 text-[color:var(--gold-300)] hover:underline">
                          {citation.code}
                        </Link>
                      ))}
                    </div>
                  </article>
                ),
              )
            )}
            {transcribe.isPending ? <LoadingBlock rows={2} /> : null}
          </div>
          <div className="border-t border-line px-4 py-3">
            <div className="flex items-end gap-2">
              <textarea
                className="input min-h-16 flex-1 resize-y"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder={t("assistant.placeholder")}
                aria-label={t("assistant.placeholder")}
              />
              <input
                ref={fileInput}
                type="file"
                accept="audio/*"
                className="sr-only"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) transcribe.mutate(file);
                  event.target.value = "";
                }}
              />
              <button
                type="button"
                className="btn-secondary"
                onClick={() => fileInput.current?.click()}
                disabled={transcribe.isPending}
                title={t("assistant.voiceInput")}
              >
                <Mic className="size-4" aria-hidden /> {t("assistant.voiceInput")}
              </button>
              <button type="button" className="btn-primary" disabled={!question.trim() || ask.isPending} onClick={submit}>
                {ask.isPending ? <Spinner /> : <Send className="size-4" aria-hidden />} {t("assistant.ask")}
              </button>
            </div>
            <p className="mt-2 flex items-center gap-1.5 text-2xs text-ink-subtle">
              <ShieldAlert className="size-3" aria-hidden /> {t("assistant.guard")}
            </p>
          </div>
        </Card>

        <Card className="lg:col-span-4">
          <CardHeader title={t("assistant.sessions")} subtitle={sessions.data ? `${sessions.data.items.length}` : undefined} />
          {sessions.isLoading ? (
            <LoadingBlock rows={5} />
          ) : sessions.error ? (
            <ErrorState message={errorMessage(sessions.error)} onRetry={() => void sessions.refetch()} />
          ) : (sessions.data?.items ?? []).length === 0 ? (
            <EmptyState title={t("assistant.noSessions")} />
          ) : (
            <ul className="divide-y divide-line/60">
              {(sessions.data?.items ?? []).map((row) => (
                <li key={row.session_id}>
                  <button type="button" className="w-full px-4 py-2 text-start hover:bg-surface-raised/60" onClick={() => setOpenSession(row.session_id)}>
                    <p className="truncate text-sm text-ink">{row.first_question || t("common.none")}</p>
                    <p className="text-2xs text-ink-subtle">
                      {t(`channels.names.${row.channel === "voice" ? "voice" : "web_form"}`, row.channel)} · {relative(row.opened_at, lang)}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {openSession && detail.data ? (
            <div className="border-t border-line px-4 py-3">
              <p className="label">{t("assistant.sessionTurns")}</p>
              <ol className="space-y-1 text-xs">
                {detail.data.turns.map((turn) => (
                  <li key={turn.seq} className="text-ink-muted">
                    <span className="text-ink-subtle">{turn.role}: </span>
                    {lang === "ar" && turn.text_ar ? turn.text_ar : turn.text}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
}
