import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AudioLines, PhoneCall, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeading } from "@/components/Shell";
import {
  Badge,
  Card,
  CardHeader,
  Drawer,
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
import type { Page, RFQSummary, RfqChannels, VoiceCallDetail, VoiceCallRow, VoiceFeatures } from "@/lib/types";

export default function VoiceCallsPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [openCall, setOpenCall] = useState<string | null>(null);
  const [rfqId, setRfqId] = useState("");
  const [supplierId, setSupplierId] = useState("");
  const [language, setLanguage] = useState("ar-EG");

  const features = useQuery({ queryKey: ["voice-features"], queryFn: () => api.get<VoiceFeatures>("/api/v1/voice/features") });

  const calls = useQuery({
    queryKey: ["voice-calls"],
    queryFn: () => api.get<{ items: VoiceCallRow[] }>("/api/v1/voice/calls?limit=100"),
    refetchInterval: 30_000,
  });

  const rfqs = useQuery({
    queryKey: ["rfqs-for-calls"],
    queryFn: () => api.get<Page<RFQSummary>>("/api/v1/rfqs?page_size=100"),
    enabled: can(PERM.voiceCall),
  });

  const channels = useQuery({
    queryKey: ["rfq-channels", rfqId],
    queryFn: () => api.get<RfqChannels>(`/api/v1/rfqs/${rfqId}/channels`),
    enabled: Boolean(rfqId),
  });

  const detail = useQuery({
    queryKey: ["voice-call", openCall],
    queryFn: () => api.get<VoiceCallDetail>(`/api/v1/voice/calls/${openCall}`),
    enabled: Boolean(openCall),
  });

  const place = useMutation({
    mutationFn: () => api.post<{ ok?: boolean; call_id?: string; error?: string }>("/api/v1/voice/calls", {
      rfq_id: rfqId,
      supplier_id: supplierId,
      language,
      mode: "ai",
    }),
    onSuccess: async (result) => {
      if (result.call_id) {
        toast.push(t("voice.placed"), "ok");
        setOpenCall(result.call_id);
      } else {
        toast.push(result.error ?? t("voice.blocked"), "danger");
      }
      await queryClient.invalidateQueries({ queryKey: ["voice-calls"] });
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const aiEnabled = features.data?.features.find((row) => row.key === "voice_ai_calls")?.enabled ?? false;

  return (
    <div className="space-y-4">
      <PageHeading eyebrow={t("voice.eyebrow")} title={t("voice.title")} subtitle={t("voice.subtitle")} />

      <div className="grid gap-4 md:grid-cols-3">
        <Card className="p-4 md:col-span-2">
          <CardHeader title={t("voice.startCall")} subtitle={t("voice.startCallHint")} />
          <div className="mt-3 grid gap-2 sm:grid-cols-4">
            <select
              className="input"
              value={rfqId}
              onChange={(event) => {
                setRfqId(event.target.value);
                setSupplierId("");
              }}
              aria-label={t("voice.rfq")}
            >
              <option value="">{t("voice.pickRfq")}</option>
              {(rfqs.data?.items ?? []).map((row) => (
                <option key={row.id} value={row.id}>
                  {row.code} · {row.title}
                </option>
              ))}
            </select>
            <select className="input" value={supplierId} onChange={(event) => setSupplierId(event.target.value)} aria-label={t("common.supplier")}>
              <option value="">{t("voice.pickSupplier")}</option>
              {(channels.data?.items ?? []).map((row) => (
                <option key={row.supplier_id} value={row.supplier_id}>
                  {(lang === "ar" && row.supplier_name_ar) || row.supplier_name}
                </option>
              ))}
            </select>
            <select className="input" value={language} onChange={(event) => setLanguage(event.target.value)} aria-label={t("common.language")}>
              {["ar-EG", "ar-SA", "en"].map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-primary"
              disabled={!rfqId || !supplierId || place.isPending || !aiEnabled}
              onClick={() => place.mutate()}
              title={aiEnabled ? undefined : t("voice.disabled")}
            >
              {place.isPending ? <Spinner /> : <PhoneCall className="size-4" aria-hidden />} {t("voice.call")}
            </button>
          </div>
          {!aiEnabled ? <p className="mt-2 text-2xs text-[color:var(--warn-ink)]">{t("voice.disabled")}</p> : null}
        </Card>

        <Card className="p-4">
          <CardHeader title={t("voice.safeguards")} />
          <ul className="mt-3 space-y-2 text-xs text-ink-muted">
            <li className="flex items-start gap-2">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-gold" aria-hidden />
              {t("voice.guardDisclosure")}
            </li>
            <li className="flex items-start gap-2">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-gold" aria-hidden />
              {t("voice.guardConsent")}
            </li>
            <li className="flex items-start gap-2">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-gold" aria-hidden />
              {t("voice.guardPolicy")}
            </li>
          </ul>
          {features.data ? <p className="mt-3 text-2xs text-ink-subtle">{`${t("voice.provider")}: ${features.data.provider}`}</p> : null}
        </Card>
      </div>

      <Card>
        <CardHeader title={t("voice.log")} subtitle={calls.data ? `${calls.data.items.length}` : undefined} />
        {calls.isLoading ? (
          <LoadingBlock rows={6} />
        ) : calls.error ? (
          <ErrorState message={errorMessage(calls.error)} onRetry={() => void calls.refetch()} />
        ) : (calls.data?.items ?? []).length === 0 ? (
          <EmptyState icon={<AudioLines className="size-5" aria-hidden />} title={t("voice.empty")} description={t("voice.emptyHint")} />
        ) : (
          <div className="overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("common.date")}</th>
                  <th>{t("common.status")}</th>
                  <th>{t("voice.consent")}</th>
                  <th>{t("voice.recording")}</th>
                  <th>{t("voice.blockedTurns")}</th>
                  <th>{t("voice.duration")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {(calls.data?.items ?? []).map((row) => (
                  <tr key={row.id}>
                    <td className="text-xs">{relative(row.created_at, lang)}</td>
                    <td>
                      <StatusChip status={row.status} />
                    </td>
                    <td>{row.consent_given ? <Badge tone="ok">{t("common.yes")}</Badge> : <Badge tone="warn">{t("common.no")}</Badge>}</td>
                    <td>{row.recording ? <Badge tone="gold">{t("voice.recording")}</Badge> : <span className="text-2xs text-ink-subtle">—</span>}</td>
                    <td className="tabular text-xs">{row.blocked_turns}</td>
                    <td className="tabular text-xs">{row.duration_seconds}s</td>
                    <td>
                      <button type="button" className="btn-ghost text-2xs" onClick={() => setOpenCall(row.id)}>
                        {t("voice.openConsole")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Drawer
        open={Boolean(openCall)}
        onClose={() => setOpenCall(null)}
        title={t("voice.console")}
        description={detail.data?.supplier_name}
        width="max-w-2xl"
        footer={
          <div className="flex justify-end">
            <button type="button" className="btn-ghost" onClick={() => setOpenCall(null)}>
              {t("common.close")}
            </button>
          </div>
        }
      >
        {detail.isLoading ? (
          <LoadingBlock rows={10} />
        ) : detail.error ? (
          <ErrorState message={errorMessage(detail.error)} onRetry={() => void detail.refetch()} />
        ) : detail.data ? (
          <div className="space-y-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip status={detail.data.status} />
              <Badge tone={detail.data.disclosure_played ? "ok" : "danger"}>
                {detail.data.disclosure_played ? t("voice.disclosed") : t("voice.notDisclosed")}
              </Badge>
              <Badge tone={detail.data.consent_given ? "ok" : "warn"}>
                {detail.data.consent_given ? t("voice.consentGiven") : t("voice.consentMissing")}
              </Badge>
              <Badge tone={detail.data.recording ? "gold" : "neutral"}>
                {detail.data.recording ? t("voice.recording") : t("voice.notRecorded")}
              </Badge>
              <span className="text-2xs text-ink-subtle">{detail.data.rfq_code}</span>
            </div>

            {detail.data.policy_snapshot.ai_agent ? (
              <section className="rounded-md border border-line p-3">
                <p className="label">{t("voice.policy")}</p>
                <p className="text-xs text-ink-muted">{detail.data.policy_snapshot.ai_agent.guard}</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  <Badge tone="info">{`${t("voice.answered")}: ${detail.data.policy_snapshot.ai_agent.topics_answered.join(", ") || "—"}`}</Badge>
                  <Badge tone={detail.data.policy_snapshot.ai_agent.blocked_turns > 0 ? "danger" : "ok"}>
                    {`${t("voice.blockedTurns")}: ${detail.data.policy_snapshot.ai_agent.blocked_turns}`}
                  </Badge>
                </div>
              </section>
            ) : null}

            <section>
              <p className="label">{t("voice.transcript")}</p>
              <ol className="space-y-2">
                {detail.data.turns.map((turn) => (
                  <li
                    key={`${turn.seq}-${turn.speaker}`}
                    className={`rounded-md border px-2 py-1.5 ${
                      turn.decision === "block" ? "border-[color:var(--danger)]/40 bg-[color:var(--danger)]/5" : "border-line"
                    }`}
                  >
                    <p className="text-2xs text-ink-subtle">
                      {turn.speaker === "ai" ? t("voice.agent") : t("common.supplier")} · {turn.topic || "—"} · {turn.decision}
                      {turn.block_code ? ` · ${turn.block_code}` : ""}
                    </p>
                    <p className="text-sm text-ink">{turn.text}</p>
                  </li>
                ))}
              </ol>
            </section>

            {detail.data.extraction_id ? (
              <p className="rounded-md border border-gold/40 bg-gold/5 px-3 py-2 text-xs text-ink-muted">{t("voice.extractionHint")}</p>
            ) : null}
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}
