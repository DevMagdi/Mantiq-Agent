import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Brand } from "@/components/Brand";

/**
 * The 404 body, shared by the standalone page and the in-shell wildcard route.
 *
 * Hidden-by-capability screens (e.g. accounting with ACCOUNTING_ENABLED=false) render this too, so
 * they are indistinguishable from a genuinely unknown URL — as designed.
 */
export function NotFoundContent() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col items-center justify-center gap-4 px-6 py-16 text-center">
      <p className="text-5xl font-semibold text-gold">404</p>
      <p className="text-sm text-ink-muted">{t("common.empty")}</p>
      <Link to="/" className="btn-secondary">
        {t("nav.dashboard")}
      </Link>
    </div>
  );
}

export default function NotFoundPage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-surface">
      <Brand />
      <NotFoundContent />
    </div>
  );
}
