import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { Brand } from "@/components/Brand";
import { Badge, Card, ErrorState, LoadingBlock, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, num } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { PublicForm } from "@/lib/types";

type PriceRow = { unit_price: string; lead_time_days?: string };

export default function PublicFormPage() {
  const { token = "" } = useParams();
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const [prices, setPrices] = useState<Record<string, PriceRow>>({});
  const [header, setHeader] = useState({ currency: "", lead_time_days: "", validity_days: "30", payment_terms: "Net 30", incoterm: "DAP", notes: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  const form = useQuery({ queryKey: ["public-form", token], queryFn: () => api.get<PublicForm>(`/api/v1/public/form/${token}`), retry: false, refetchOnWindowFocus: false });

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/v1/public/form/${token}`, {
        currency: header.currency || form.data?.rfq.currency,
        lead_time_days: header.lead_time_days || undefined,
        validity_days: header.validity_days || undefined,
        payment_terms: header.payment_terms,
        incoterm: header.incoterm,
        notes: header.notes || undefined,
        lines: (form.data?.lines ?? []).map((line) => ({
          description: line.description,
          quantity: line.quantity,
          uom: line.uom,
          unit_price: prices[line.id]?.unit_price ?? "",
        })),
      });
      setDone(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-surface px-4 py-8">
      <div className="mx-auto max-w-3xl space-y-4">
        <header className="flex items-center justify-between">
          <Brand />
          <Badge tone="gold">{t("publicForm.subtitle")}</Badge>
        </header>

        {form.isLoading ? (
          <Card><LoadingBlock rows={6} /></Card>
        ) : form.error ? (
          <Card><ErrorState message={errorMessage(form.error)} /></Card>
        ) : done ? (
          <Card className="p-8 text-center">
            <CheckCircle2 className="mx-auto size-10 text-[color:var(--ok-ink)]" aria-hidden />
            <h1 className="mt-3 text-lg font-semibold">{t("publicForm.success")}</h1>
            <p className="mt-1 text-sm text-ink-muted">{form.data?.supplier.name}</p>
          </Card>
        ) : form.data ? (
          <>
            <Card className="p-5">
              <p className="font-mono text-xs text-gold">{form.data.rfq.code}</p>
              <h1 className="mt-1 text-xl font-semibold text-ink">{form.data.rfq.title}</h1>
              <p className="mt-1 text-sm text-ink-muted">
                {form.data.supplier.name} · {t("publicForm.expires")} {date(form.data.expires_at, lang)}
              </p>
              <div className="mt-3 grid gap-2 text-sm sm:grid-cols-3">
                <div>
                  <p className="label">{t("common.currency")}</p>
                  <p>{form.data.rfq.currency}</p>
                </div>
                <div>
                  <p className="label">{t("common.deadline")}</p>
                  <p className="tabular">{date(form.data.rfq.deadline, lang)}</p>
                </div>
                <div>
                  <p className="label">{t("publicForm.payment")}</p>
                  <p>{form.data.rfq.payment_terms}</p>
                </div>
              </div>
            </Card>

            <Card className="overflow-hidden">
              <table className="table-dense">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>{t("requests.lines")}</th>
                    <th className="text-end">{t("common.quantity")}</th>
                    <th>{t("publicForm.unitPrice")}</th>
                  </tr>
                </thead>
                <tbody>
                  {form.data.lines.map((line) => (
                    <tr key={line.id}>
                      <td className="text-xs text-ink-subtle">{line.line_no}</td>
                      <td className="text-sm">{line.description}</td>
                      <td className="text-end tabular text-sm">{num(line.quantity, lang, 2)} {line.uom}</td>
                      <td>
                        <input
                          className="input tabular"
                          inputMode="decimal"
                          dir="ltr"
                          placeholder={`${t("common.price")} / ${line.uom}`}
                          value={prices[line.id]?.unit_price ?? ""}
                          onChange={(event) =>
                            setPrices((current) => ({ ...current, [line.id]: { ...current[line.id], unit_price: event.target.value } }))
                          }
                          aria-label={`${line.description} unit price`}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <Card className="grid gap-3 p-4 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="lead">{t("publicForm.leadTime")}</label>
                <input id="lead" className="input tabular" inputMode="numeric" value={header.lead_time_days} onChange={(event) => setHeader({ ...header, lead_time_days: event.target.value })} />
              </div>
              <div>
                <label className="label" htmlFor="validity">{t("publicForm.validity")}</label>
                <input id="validity" className="input tabular" inputMode="numeric" value={header.validity_days} onChange={(event) => setHeader({ ...header, validity_days: event.target.value })} />
              </div>
              <div>
                <label className="label" htmlFor="payment">{t("publicForm.payment")}</label>
                <input id="payment" className="input" value={header.payment_terms} onChange={(event) => setHeader({ ...header, payment_terms: event.target.value })} />
              </div>
              <div>
                <label className="label" htmlFor="incoterm">{t("publicForm.incoterm")}</label>
                <select id="incoterm" className="input" value={header.incoterm} onChange={(event) => setHeader({ ...header, incoterm: event.target.value })}>
                  {["DAP", "DDP", "FOB", "CIF", "EXW", "FCA"].map((value) => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </select>
              </div>
              <div className="sm:col-span-2">
                <label className="label" htmlFor="notes">{t("publicForm.notes")}</label>
                <textarea id="notes" className="input min-h-20" value={header.notes} onChange={(event) => setHeader({ ...header, notes: event.target.value })} />
              </div>
              {error ? (
                <p role="alert" className="sm:col-span-2 text-sm text-[color:var(--danger-ink)]">{error}</p>
              ) : null}
              <div className="sm:col-span-2 flex items-center justify-between">
                <p className="flex items-center gap-1.5 text-2xs text-ink-subtle">
                  <ShieldCheck className="size-3.5" aria-hidden /> {t("auth.trust")}
                </p>
                <button type="button" className="btn-primary" disabled={busy || Object.values(prices).every((row) => !row?.unit_price)} onClick={() => void submit()}>
                  {busy ? <Spinner /> : null}
                  {busy ? t("publicForm.submitting") : t("publicForm.submit")}
                </button>
              </div>
            </Card>
          </>
        ) : null}
      </div>
    </div>
  );
}
