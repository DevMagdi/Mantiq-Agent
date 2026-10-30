import { Lock } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import { Card } from "./ui";
import { useAuth } from "@/lib/session";

/**
 * Renders children only when the signed-in user holds the permission.
 *
 * The API is the real gate; this exists so a `requester` never sees a screen whose requests
 * would 403. The fallback is a deliberately calm panel, not an error: nothing is broken.
 */
export function RequirePermission({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(permission)) return <NoAccess permission={permission} />;
  return <>{children}</>;
}

export function NoAccess({ permission }: { permission: string }) {
  const { t } = useTranslation();
  return (
    <Card className="mx-auto mt-6 max-w-lg px-6 py-10 text-center">
      <div className="mx-auto flex size-12 items-center justify-center rounded-full border border-line bg-surface-raised text-gold">
        <Lock className="size-5" aria-hidden />
      </div>
      <h1 className="mt-3 text-base font-semibold text-ink">{t("access.deniedTitle")}</h1>
      <p className="mt-1 text-sm text-ink-muted">{t("access.deniedBody")}</p>
      <p className="mt-2 font-mono text-2xs text-ink-subtle">{permission}</p>
      <Link to="/" className="btn-secondary mt-5 inline-flex">
        {t("access.backHome")}
      </Link>
    </Card>
  );
}

/**
 * Small inline notice for a card that would otherwise call a protected endpoint.
 * Used by the dashboard so a `requester` sees an explained empty state instead of a 403.
 */
export function RestrictedPanel({ title, permission }: { title: string; permission: string }) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      <Lock className="size-4 text-ink-subtle" aria-hidden />
      <p className="text-sm font-medium text-ink">{title}</p>
      <p className="max-w-xs text-2xs text-ink-muted">{t("access.cardBody")}</p>
      <p className="font-mono text-3xs text-ink-subtle">{permission}</p>
    </div>
  );
}
