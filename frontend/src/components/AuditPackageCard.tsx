import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileCheck2, FileWarning, Package, ShieldCheck, ShieldQuestion } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
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
import { dateTime } from "@/lib/format";
import { PERM } from "@/lib/permissions";
import { useAuth } from "@/lib/session";
import type { AppLang } from "@/i18n";
import type { AuditPackageEnvelope } from "@/lib/types";

/**
 * The auditor's document package for one purchase order.
 *
 * Assembling seals every component with a SHA-256 hash (they are verified again on read), and only
 * an auditor holding `audit_package:review` can accept or query the evidence. Accepting is what
 * unblocks the accounting posting, so this card is the visible hand-off between procurement and
 * the books.
 */
export function AuditPackageCard({ poId, onChanged }: { poId: string; onChanged?: () => Promise<void> | void }) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");
  const [openComponent, setOpenComponent] = useState<string | null>(null);

  const readable = can(PERM.auditPackageRead);
  const query = useQuery({
    queryKey: ["audit-package", poId],
    queryFn: () => api.get<AuditPackageEnvelope>(`/api/v1/purchase-orders/${poId}/audit-package`),
    enabled: readable && Boolean(poId),
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["audit-package", poId] });
    await queryClient.invalidateQueries({ queryKey: ["lifecycle", poId] });
    await onChanged?.();
  };

  const assemble = useMutation({
    mutationFn: () => api.post(`/api/v1/purchase-orders/${poId}/audit-package`, {}),
    onSuccess: async () => {
      toast.push(t("accounting.audit.toast.assembled"), "ok");
      await refresh();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const review = useMutation({
    mutationFn: (decision: "accepted" | "queried") => {
      const packageId = query.data?.package?.id;
      if (!packageId) throw new Error("no package");
      return api.post(`/api/v1/audit-packages/${packageId}/review`, { decision, comment });
    },
    onSuccess: async (_data, decision) => {
      toast.push(decision === "accepted" ? t("accounting.audit.toast.accepted") : t("accounting.audit.toast.queried"), "ok");
      setComment("");
      await refresh();
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  if (!readable) return null;

  const envelope = query.data;
  const pkg = envelope?.package ?? null;
  const verification = envelope?.verification;
  const intact = verification?.intact ?? null;

  return (
    <Card>
      <CardHeader
        title={t("accounting.audit.title")}
        subtitle={t("accounting.audit.subtitle")}
        action={
          pkg ? (
            <StatusChip status={pkg.status} label={lang === "ar" ? pkg.status_label_ar : pkg.status_label} />
          ) : null
        }
      />
      {query.isLoading ? (
        <LoadingBlock rows={3} />
      ) : query.isError ? (
        <ErrorState message={errorMessage(query.error)} onRetry={() => void query.refetch()} />
      ) : !pkg ? (
        <div className="space-y-3 p-4">
          <EmptyState
            title={t("accounting.audit.empty")}
            description={lang === "ar" ? envelope?.hint_ar : envelope?.hint_en}
            icon={<Package className="size-5" />}
          />
          <div className="flex justify-center">
            <button type="button" className="btn-secondary btn-sm" disabled={assemble.isPending}
                    onClick={() => assemble.mutate()}>
              {assemble.isPending ? <Spinner /> : <FileCheck2 className="size-3.5" aria-hidden />}
              {t("accounting.audit.assemble")}
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={intact === false ? "danger" : "ok"}>
              {intact === false ? (
                <><FileWarning className="me-1 size-3" aria-hidden />{t("accounting.audit.tampered")}</>
              ) : (
                <><ShieldCheck className="me-1 size-3" aria-hidden />{t("accounting.audit.intact")}</>
              )}
            </Badge>
            <span className="tabular text-2xs text-ink-subtle">
              {t("accounting.audit.components", { count: pkg.component_count })} · {pkg.code}
            </span>
            {pkg.generated_at ? (
              <span className="text-2xs text-ink-subtle">{dateTime(pkg.generated_at, lang)}</span>
            ) : null}
            <button type="button" className="btn-ghost btn-sm ms-auto" disabled={assemble.isPending}
                    onClick={() => assemble.mutate()}>
              {assemble.isPending ? <Spinner /> : null}
              {t("accounting.audit.reassemble")}
            </button>
          </div>

          <ul className="divide-y divide-line/70 rounded-md border border-line/70">
            {pkg.components.map((component) => (
              <li key={`${component.kind}-${component.ref_id ?? component.hash.slice(0, 8)}`}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-start hover:bg-surface-raised/40"
                  onClick={() => setOpenComponent(openComponent === component.hash ? null : component.hash)}
                  aria-expanded={openComponent === component.hash}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-2xs text-ink">
                      {lang === "ar" ? component.label_ar : component.label_en}
                    </span>
                    <span className="tabular block truncate text-[0.625rem] text-ink-subtle">
                      {component.hash.slice(0, 16)}…
                    </span>
                  </span>
                  <Badge tone="neutral">{component.kind.replaceAll("_", " ")}</Badge>
                </button>
                {openComponent === component.hash ? (
                  <pre className="max-h-56 overflow-auto border-t border-line/70 bg-surface px-2.5 py-2 text-[0.625rem] leading-relaxed text-ink-muted">
                    {JSON.stringify(component.payload ?? {}, null, 2)}
                  </pre>
                ) : null}
              </li>
            ))}
          </ul>

          {pkg.reviewed_at ? (
            <p className="text-2xs text-ink-muted">
              {t("accounting.audit.reviewedBy", { at: dateTime(pkg.reviewed_at, lang) })}
              {pkg.comment ? ` · ${pkg.comment}` : ""}
            </p>
          ) : null}

          {can(PERM.auditPackageReview) && pkg.status !== "reviewed" ? (
            <div className="space-y-2 rounded-md border border-line/70 bg-surface-raised/30 p-2.5">
              <label className="block">
                <span className="eyebrow">{t("accounting.audit.comment")}</span>
                <textarea className="input mt-1 w-full" rows={2} value={comment}
                          onChange={(event) => setComment(event.target.value)}
                          placeholder={t("accounting.audit.commentHint")} />
              </label>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-primary btn-sm" disabled={review.isPending}
                        onClick={() => review.mutate("accepted")}>
                  {review.isPending ? <Spinner /> : <ShieldCheck className="size-3.5" aria-hidden />}
                  {t("accounting.audit.accept")}
                </button>
                <button type="button" className="btn-secondary btn-sm"
                        disabled={review.isPending || !comment.trim()}
                        onClick={() => review.mutate("queried")}>
                  <ShieldQuestion className="size-3.5" aria-hidden />
                  {t("accounting.audit.query")}
                </button>
              </div>
            </div>
          ) : null}

          <p className="text-2xs text-ink-subtle">{t("accounting.audit.footer")}</p>
        </div>
      )}
    </Card>
  );
}
