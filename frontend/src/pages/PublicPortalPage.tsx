import { useQuery } from "@tanstack/react-query";
import { Building2, CheckCircle2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router-dom";
import { Brand } from "@/components/Brand";
import { Badge, Card, ErrorState, LoadingBlock, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { PublicPortal } from "@/lib/types";

const CATEGORIES = ["STEEL", "PACKAGING", "LUBRICANTS", "SPARE_PARTS", "ELECTRICAL", "MRO", "CHEMICALS"];

export default function PublicPortalPage() {
  const { token = "" } = useParams();
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const [website, setWebsite] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  const portal = useQuery({ queryKey: ["public-portal", token], queryFn: () => api.get<PublicPortal>(`/api/v1/public/supplier/${token}`), retry: false });

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/v1/public/supplier/${token}`, { categories: selected, website: website || undefined });
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-surface px-4 py-8">
      <div className="mx-auto max-w-2xl space-y-4">
        <header className="flex items-center justify-between">
          <Brand />
          <Badge tone="gold">{t("publicPortal.title")}</Badge>
        </header>

        {portal.isLoading ? (
          <Card><LoadingBlock rows={5} /></Card>
        ) : portal.error ? (
          <Card><ErrorState message={errorMessage(portal.error)} /></Card>
        ) : portal.data ? (
          <>
            <Card className="p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h1 className="text-xl font-semibold text-ink">
                    {lang === "ar" && portal.data.supplier.name_ar ? portal.data.supplier.name_ar : portal.data.supplier.name}
                  </h1>
                  <p className="mt-1 flex items-center gap-1.5 text-sm text-ink-muted">
                    <Building2 className="size-4" aria-hidden /> {portal.data.supplier.country} · {portal.data.supplier.city}
                  </p>
                </div>
                <Badge tone="ok">{portal.data.supplier.status}</Badge>
              </div>
              <p className="mt-3 text-sm text-ink-muted">{portal.data.message}</p>
              <p className="mt-1 text-2xs text-ink-subtle">
                {t("publicForm.expires")} {date(portal.data.expires_at, lang)}
              </p>
            </Card>

            <Card className="p-5">
              <p className="label">{t("publicPortal.categories")}</p>
              <div className="flex flex-wrap gap-2">
                {CATEGORIES.map((category) => {
                  const active = selected.includes(category) || portal.data?.supplier.categories.includes(category);
                  return (
                    <button
                      key={category}
                      type="button"
                      aria-pressed={active}
                      className={`chip ${active ? "border-gold bg-gold/15 text-[color:var(--gold-300)]" : "text-ink-muted"}`}
                      onClick={() =>
                        setSelected((current) =>
                          current.includes(category) ? current.filter((item) => item !== category) : [...current, category],
                        )
                      }
                    >
                      {category}
                    </button>
                  );
                })}
              </div>
              <div className="mt-4">
                <label className="label" htmlFor="website">{t("publicPortal.website")}</label>
                <input id="website" className="input" dir="ltr" placeholder="https://" value={website} onChange={(event) => setWebsite(event.target.value)} />
              </div>

              {portal.data.contacts.length > 0 ? (
                <div className="mt-4">
                  <p className="label">{t("suppliers.title")}</p>
                  <ul className="space-y-1 text-xs">
                    {portal.data.contacts.map((contact) => (
                      <li key={`${contact.name}-${contact.email}`} className="flex items-center justify-between rounded border border-line px-2 py-1">
                        <span>{contact.name || contact.role}</span>
                        <span className="text-ink-subtle" dir="ltr">{contact.email} {contact.phone}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {error ? <p role="alert" className="mt-3 text-sm text-[color:var(--danger)]">{error}</p> : null}
              {saved ? (
                <p className="mt-3 flex items-center gap-1.5 text-sm text-[color:var(--ok)]">
                  <CheckCircle2 className="size-4" aria-hidden /> {t("publicPortal.pending")}
                </p>
              ) : null}
              <div className="mt-4 flex justify-end">
                <button type="button" className="btn-primary" onClick={() => void submit()} disabled={busy}>
                  {busy ? <Spinner /> : null}
                  {t("publicPortal.submit")}
                </button>
              </div>
            </Card>
          </>
        ) : null}
      </div>
    </div>
  );
}
