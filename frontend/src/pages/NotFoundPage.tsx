import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Brand } from "@/components/Brand";

export default function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-surface px-6 text-center">
      <Brand />
      <p className="text-5xl font-semibold text-gold">404</p>
      <p className="text-sm text-ink-muted">{t("common.empty")}</p>
      <Link to="/" className="btn-secondary">
        {t("nav.dashboard")}
      </Link>
    </div>
  );
}
