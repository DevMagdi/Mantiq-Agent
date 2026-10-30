import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, RotateCcw, XCircle } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { PageHeading } from "@/components/Shell";
import { Card, EmptyState, ErrorState, LoadingBlock, Spinner, StatusChip, useToast } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/session";
import { PERM } from "@/lib/permissions";
import { date, money, relative } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { Approval, Page } from "@/lib/types";

type Decision = "approve" | "reject" | "request_revision";

export default function ApprovalsPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const queryClient = useQueryClient();
  const toast = useToast();
  const [comments, setComments] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const { can } = useAuth();
  const list = useQuery({
    queryKey: ["approvals"],
    queryFn: () => api.get<Page<Approval>>("/api/v1/approvals?page_size=100"),
    enabled: can(PERM.approvalRead),
  });

  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: Decision }) =>
      api.post(`/api/v1/approvals/${id}/decide`, { decision, comment: comments[id] ?? "" }),
    onSuccess: async () => {
      toast.push(t("common.save"), "ok");
      await queryClient.invalidateQueries({ queryKey: ["approvals"] });
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
    onSettled: () => setBusy(null),
  });

  if (list.isLoading) return <LoadingBlock rows={6} />;
  if (list.error) return <ErrorState message={errorMessage(list.error)} onRetry={() => void list.refetch()} />;

  const items = list.data?.items ?? [];
  const pending = items.filter((row) => row.status === "pending");

  return (
    <div className="space-y-4">
      <PageHeading title={t("approvals.title")} subtitle={t("approvals.subtitle")} />

      {pending.length === 0 ? (
        <Card>
          <EmptyState title={t("approvals.empty")} description={t("approvals.subtitle")} icon={<CheckCircle2 className="size-5" />} />
        </Card>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {pending.map((approval) => (
            <Card key={approval.id} className="flex flex-col">
              <div className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
                <div className="min-w-0">
                  <Link to={`/rfqs/${approval.rfq_id}`} className="truncate text-sm font-semibold text-ink hover:text-gold">
                    {approval.rfq_title}
                  </Link>
                  <p className="text-2xs text-ink-subtle">
                    {approval.rfq_code} · {t("approvals.step")} {approval.step_no} · {approval.approver_role}
                  </p>
                </div>
                <StatusChip status={approval.status} />
              </div>
              <div className="grid grid-cols-3 gap-2 px-4 py-3 text-sm">
                <div>
                  <p className="label">{t("approvals.amount")}</p>
                  <p className="tabular">{approval.amount ? money(approval.amount, approval.currency ?? "EGP", lang) : "—"}</p>
                </div>
                <div>
                  <p className="label">{t("approvals.due")}</p>
                  <p className="tabular text-xs">{date(approval.due_at, lang)}</p>
                </div>
                <div>
                  <p className="label">{t("approvals.sla")}</p>
                  <p className="text-xs">{approval.due_at ? relative(approval.due_at, lang) : "—"}</p>
                </div>
              </div>
              <div className="mt-auto border-t border-line px-4 py-3">
                <input
                  className="input mb-2"
                  placeholder={t("approvals.comment")}
                  value={comments[approval.id] ?? ""}
                  onChange={(event) => setComments((current) => ({ ...current, [approval.id]: event.target.value }))}
                  aria-label={t("approvals.comment")}
                />
                {approval.chain && approval.chain.length > 1 ? (
                  <ol className="mb-2 flex flex-wrap gap-1 text-2xs">
                    {approval.chain.map((step) => (
                      <li key={step.step_no} className="chip">
                        {step.step_no}. {step.role.replaceAll("_", " ")} · {step.status.replaceAll("_", " ")}
                      </li>
                    ))}
                  </ol>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={busy === approval.id || approval.can_decide === false}
                    onClick={() => {
                      setBusy(approval.id);
                      decide.mutate({ id: approval.id, decision: "approve" });
                    }}
                  >
                    {busy === approval.id ? <Spinner /> : <CheckCircle2 className="size-4" aria-hidden />} {t("approvals.approve")}
                  </button>
                  <button
                    type="button"
                    className="btn-secondary"
                    disabled={busy === approval.id || approval.can_decide === false}
                    onClick={() => {
                      setBusy(approval.id);
                      decide.mutate({ id: approval.id, decision: "request_revision" });
                    }}
                  >
                    <RotateCcw className="size-4" aria-hidden /> {t("approvals.revise")}
                  </button>
                  <button
                    type="button"
                    className="btn-danger"
                    disabled={busy === approval.id || approval.can_decide === false}
                    onClick={() => {
                      setBusy(approval.id);
                      decide.mutate({ id: approval.id, decision: "reject" });
                    }}
                  >
                    <XCircle className="size-4" aria-hidden /> {t("approvals.reject")}
                  </button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Card className="overflow-hidden">
        <div className="card-header">
          <h2 className="text-sm font-semibold">{t("approvals.title")}</h2>
        </div>
        <div className="max-h-80 overflow-y-auto">
          <table className="table-dense">
            <thead>
              <tr>
                <th>RFQ</th>
                <th>{t("approvals.step")}</th>
                <th>{t("common.status")}</th>
                <th>{t("approvals.amount")}</th>
                <th>{t("common.date")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((approval) => (
                <tr key={approval.id}>
                  <td>
                    <Link to={`/rfqs/${approval.rfq_id}`} className="text-xs text-gold hover:underline">
                      {approval.rfq_code}
                    </Link>
                  </td>
                  <td className="text-xs">{approval.step_no} · {approval.approver_role}</td>
                  <td><StatusChip status={approval.status} /></td>
                  <td className="tabular text-xs">{approval.amount ? money(approval.amount, approval.currency ?? "EGP", lang) : "—"}</td>
                  <td className="text-xs text-ink-muted">{date(approval.decided_at ?? approval.due_at, lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
