import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquarePlus, Plus, Sparkles } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { PageHeading } from "@/components/Shell";
import { DataTable } from "@/components/DataTable";
import { Badge, Card, cn, Drawer, EmptyState, ErrorState, LoadingBlock, Progress, Spinner, StatusChip, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, money, num, percent, relative } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { Clarification, PRLine, Page, PurchaseRequest } from "@/lib/types";
import type { ColumnDef } from "@tanstack/react-table";

export default function RequestsPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [intakeOpen, setIntakeOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["requests"],
    queryFn: () => api.get<Page<PurchaseRequest>>("/api/v1/requests?page_size=100"),
  });

  const detail = useQuery({
    queryKey: ["request", selected],
    queryFn: () => api.get<PurchaseRequest>(`/api/v1/requests/${selected}`),
    enabled: Boolean(selected),
  });

  const columns: ColumnDef<PurchaseRequest, unknown>[] = [
    {
      accessorKey: "code",
      header: "Code",
      cell: ({ row }) => <span className="font-mono text-xs text-gold">{row.original.code}</span>,
    },
    {
      accessorKey: "title",
      header: t("requests.title"),
      cell: ({ row }) => (
        <div className="max-w-[26rem]">
          <p className="truncate text-ink">{row.original.title}</p>
          <p className="text-2xs text-ink-subtle">
            {row.original.source} · {relative(row.original.created_at, lang)}
          </p>
        </div>
      ),
    },
    {
      accessorKey: "status",
      header: t("common.status"),
      cell: ({ row }) => <StatusChip status={row.original.status} label={t(`requests.statuses.${row.original.status}`)} />,
    },
    {
      accessorKey: "total_estimate",
      header: t("common.total"),
      cell: ({ row }) => <span className="tabular">{money(row.original.total_estimate, row.original.currency, lang)}</span>,
    },
    {
      accessorKey: "confidence",
      header: t("requests.confidence"),
      cell: ({ row }) => {
        const value = (row.original.confidence ?? 0) * 100;
        return (
          <div className="flex w-24 items-center gap-2">
            <Progress value={value} tone={value >= 75 ? "ok" : "warn"} />
            <span className="text-2xs text-ink-muted">{percent(value)}</span>
          </div>
        );
      },
    },
    {
      accessorKey: "needed_by",
      header: t("requests.neededBy"),
      cell: ({ row }) => <span className="tabular text-xs">{date(row.original.needed_by, lang)}</span>,
    },
  ];

  return (
    <div className="space-y-4">
      <PageHeading
        title={t("requests.title")}
        subtitle={t("requests.subtitle")}
        actions={
          <button type="button" className="btn-primary" onClick={() => setIntakeOpen(true)}>
            <Plus className="size-4" aria-hidden />
            {t("requests.newRequest")}
          </button>
        }
      />

      <Card className="overflow-hidden">
        {list.isLoading ? (
          <LoadingBlock rows={6} />
        ) : list.error ? (
          <ErrorState message={errorMessage(list.error)} onRetry={() => void list.refetch()} />
        ) : (
          <DataTable
            data={list.data?.items ?? []}
            columns={columns}
            onRowClick={(row) => setSelected(row.id)}
            searchPlaceholder={`${t("common.search")} · ${t("requests.title")}`}
            empty={<EmptyState title={t("common.empty")} description={t("requests.subtitle")} action={<button className="btn-secondary" onClick={() => setIntakeOpen(true)}>{t("requests.newRequest")}</button>} icon={<MessageSquarePlus className="size-5" />} />}
          />
        )}
      </Card>

      <IntakeDrawer
        open={intakeOpen}
        onClose={() => setIntakeOpen(false)}
        onCreated={async (id) => {
          await queryClient.invalidateQueries({ queryKey: ["requests"] });
          setIntakeOpen(false);
          setSelected(id);
        }}
      />

      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={detail.data?.code ?? t("common.loading")}
        description={detail.data?.title}
        width="max-w-2xl"
        footer={
          detail.data ? (
            <div className="flex items-center justify-between gap-2">
              <Badge tone={detail.data.status === "ready" ? "ok" : "warn"}>{t(`requests.statuses.${detail.data.status}`)}</Badge>
              {detail.data.status === "ready" ? (
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => navigate(`/rfqs?pr=${detail.data?.id ?? ""}`)}
                >
                  {t("rfqs.newRfq")}
                </button>
              ) : null}
            </div>
          ) : null
        }
      >
        {detail.isLoading ? (
          <LoadingBlock rows={6} />
        ) : detail.error ? (
          <ErrorState message={errorMessage(detail.error)} onRetry={() => void detail.refetch()} />
        ) : detail.data ? (
          <RequestDetail
            request={detail.data}
            onChanged={async () => {
              await detail.refetch();
              await queryClient.invalidateQueries({ queryKey: ["requests"] });
              toast.push("Saved", "ok");
            }}
          />
        ) : null}
      </Drawer>
    </div>
  );
}

function IntakeDrawer({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => Promise<void> }) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [plant, setPlant] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");

  const plants = useQuery({
    queryKey: ["plants"],
    queryFn: () => api.get<Page<{ id: string; code: string; name: string }>>("/api/v1/plants"),
    enabled: open,
  });

  const mutation = useMutation({
    mutationFn: () =>
      api.post<{ id: string; code: string; status: string }>("/api/v1/intake/text", {
        text,
        title: title || undefined,
        ...(plant ? { plant_code: plant } : {}),
      }),
    onSuccess: async (result) => {
      setText("");
      await onCreated(result.id);
    },
    onError: (err) => setError(errorMessage(err)),
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={t("requests.newRequest")}
      description={t("requests.textIntakeHint")}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={text.trim().length < 12 || mutation.isPending}
            onClick={() => {
              setError("");
              mutation.mutate();
            }}
          >
            {mutation.isPending ? <Spinner /> : <Sparkles className="size-4" aria-hidden />}
            {mutation.isPending ? t("requests.analyzing") : t("requests.analyze")}
          </button>
        </div>
      }
    >
      <div className="space-y-3">
        <div>
          <label className="label" htmlFor="intake-text">
            {t("requests.textIntake")}
          </label>
          <textarea
            id="intake-text"
            className="input min-h-32"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={t("requests.textIntakeHint")}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="plant">
              {t("requests.plant")}
            </label>
            <select id="plant" className="input" value={plant} onChange={(event) => setPlant(event.target.value)}>
              <option value="">{t("common.all")}</option>
              {(plants.data?.items ?? []).map((row) => (
                <option key={row.id} value={row.code}>
                  {row.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="request-title">
              {t("requests.title")}
            </label>
            <input id="request-title" className="input" value={title} onChange={(event) => setTitle(event.target.value)} placeholder={t("common.optional")} />
          </div>
        </div>
        {error ? (
          <p role="alert" className="text-sm text-[color:var(--danger-ink)]">
            {error}
          </p>
        ) : null}
        <p className="text-2xs text-ink-subtle">
          {`${t("common.optional")}: ${t("requests.confidence")} · ${t("requests.clarifications")}`}
        </p>
      </div>
    </Drawer>
  );
}

function RequestDetail({ request, onChanged }: { request: PurchaseRequest; onChanged: () => Promise<void> }) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const answer = async (question: Clarification) => {
    setBusy(question.id);
    try {
      await api.post(`/api/v1/requests/${request.id}/clarifications/${question.id}/answer`, {
        answer: answers[question.id] ?? "",
      });
      await onChanged();
    } finally {
      setBusy(null);
    }
  };

  const submit = async () => {
    setBusy("submit");
    try {
      await api.post(`/api/v1/requests/${request.id}/submit`);
      await onChanged();
    } finally {
      setBusy(null);
    }
  };

  const normalize = async (lineId: string, taxonomyCode: string) => {
    if (!taxonomyCode) return;
    setBusy(lineId);
    try {
      await api.post(`/api/v1/requests/${request.id}/lines/${lineId}/normalize`, { taxonomy_code: taxonomyCode });
      await onChanged();
    } finally {
      setBusy(null);
    }
  };

  const questions = request.clarifications ?? [];

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <p className="label">{t("common.total")}</p>
          <p className="tabular">{money(request.total_estimate, request.currency, lang)}</p>
        </div>
        <div>
          <p className="label">{t("requests.confidence")}</p>
          <p className="tabular">{percent((request.confidence ?? 0) * 100)}</p>
        </div>
        <div>
          <p className="label">{t("requests.neededBy")}</p>
          <p>{date(request.needed_by, lang)}</p>
        </div>
        <div>
          <p className="label">{t("common.details")}</p>
          <p className="text-xs text-ink-muted">{request.source}</p>
        </div>
      </div>

      {questions.length > 0 ? (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">{t("requests.clarifications")}</h3>
          {questions.map((question) => (
            <div key={question.id} className={cn("rounded-md border p-3", question.status === "open" ? "border-[color:var(--warn)]/40 bg-[color:var(--warn)]/5" : "border-line")}>
              <p className="text-sm text-ink">{lang === "ar" && question.question_ar ? question.question_ar : question.question}</p>
              {question.status === "open" ? (
                <div className="mt-2 flex gap-2">
                  <input
                    className="input"
                    value={answers[question.id] ?? ""}
                    onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))}
                    placeholder={t("requests.answer")}
                    aria-label={question.question}
                  />
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={!answers[question.id]?.trim() || busy === question.id}
                    onClick={() => void answer(question)}
                  >
                    {busy === question.id ? <Spinner /> : null}
                    {t("common.save")}
                  </button>
                </div>
              ) : (
                <p className="mt-1 text-xs text-ink-muted">
                  {t("requests.answer")}: {question.answer ?? "—"}
                </p>
              )}
            </div>
          ))}
          {request.status === "clarifying" ? (
            <button type="button" className="btn-primary" disabled={busy === "submit"} onClick={() => void submit()}>
              {busy === "submit" ? <Spinner /> : null}
              {t("requests.submitForRfq")}
            </button>
          ) : null}
        </section>
      ) : null}

      <section>
        <h3 className="mb-2 text-sm font-semibold text-ink">{t("requests.lines")}</h3>
        <table className="table-dense">
          <thead>
            <tr>
              <th>#</th>
              <th>{t("requests.lines")}</th>
              <th>{t("common.quantity")}</th>
              <th>{t("common.category")}</th>
              <th>{t("common.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {(request.lines ?? []).map((line) => (
              <tr key={line.id}>
                <td className="tabular text-xs text-ink-subtle">{line.line_no}</td>
                <td className="max-w-xs">
                  <p className="truncate text-sm">{line.description}</p>
                  {line.taxonomy_code ? <p className="font-mono text-2xs text-gold">{line.taxonomy_code}</p> : null}
                </td>
                <td className="tabular text-sm">
                  {num(line.quantity, lang, 2)} {line.uom}
                </td>
                <td className="text-xs text-ink-muted">{line.category_code ?? "—"}</td>
                <td>
                  <LineNormalize
                    line={line}
                    busy={busy === line.id}
                    onNormalize={(code) => void normalize(line.id, code)}
                  />
                </td>
              </tr>
            ))}
            {(request.lines ?? []).length === 0 ? (
              <tr>
                <td colSpan={5} className="py-6 text-center text-sm text-ink-muted">
                  {t("common.empty")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>
    </div>
  );
}

/**
 * Inline taxonomy normaliser: shows the classifier's candidates and lets the
 * buyer confirm one. The buyer's choice is what `POST .../normalize` persists —
 * the model only proposes, a human disposes.
 */
function LineNormalize({ line, busy, onNormalize }: { line: PRLine; busy: boolean; onNormalize: (code: string) => void }) {
  const { t } = useTranslation();
  const candidates = line.candidates ?? [];
  const [code, setCode] = useState(line.taxonomy_code ?? candidates[0]?.taxonomy_code ?? "");
  return (
    <div className="flex items-center gap-1">
      <select
        className="input w-40 py-1 text-2xs"
        value={code}
        onChange={(event) => setCode(event.target.value)}
        aria-label={`${line.description} taxonomy`}
      >
        <option value="">{t("common.none")}</option>
        {candidates.map((candidate) => (
          <option key={candidate.taxonomy_code} value={candidate.taxonomy_code}>
            {`${candidate.taxonomy_code} · ${percent(candidate.confidence * 100)}`}
          </option>
        ))}
        {line.taxonomy_code && !candidates.some((candidate) => candidate.taxonomy_code === line.taxonomy_code) ? (
          <option value={line.taxonomy_code}>{line.taxonomy_code}</option>
        ) : null}
      </select>
      <button type="button" className="btn-ghost text-2xs" onClick={() => onNormalize(code)} disabled={busy || !code}>
        {busy ? <Spinner /> : null}
        {t("requests.normalize")}
      </button>
    </div>
  );
}
