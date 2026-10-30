import { ChevronsLeft, ChevronsRight, LogOut } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";
import { NAV_GROUPS } from "@/lib/nav";
import { cn } from "./ui";
import { Brand } from "./Brand";

/**
 * The rail.
 *
 * Shape: a vertical gradient one step darker than the page, a single hairline against the content,
 * and four labelled groups whose labels carry a fading rule so the list never reads as one block.
 * Gold appears on exactly one item — the current one — and nowhere else.
 *
 * States: hover raises the item 3.5% white and warms the icon; active draws a 2px gold spine, a
 * gold wash that fades out, an inset gold hairline and a gold icon; focus-visible keeps the global
 * gold ring. Collapsed items become 44px squares that keep the spine and expose the label through
 * `title` + `aria-label`, so nothing is lost for a screen reader or a hovering user.
 *
 * RTL: every offset is logical (`start`/`end`, `inset-inline`), so Arabic mirrors with no overrides.
 */
export function Sidebar({
  can,
  tenantName,
  plan,
  onSignOut,
  collapsed,
  onToggleCollapse,
  onNavigate,
}: {
  can: (permission: string) => boolean;
  tenantName: string;
  plan: string;
  onSignOut: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const groups = useMemo(
    () =>
      NAV_GROUPS.map((group) => ({ ...group, items: group.items.filter((item) => can(item.permission)) })).filter(
        (group) => group.items.length > 0,
      ),
    [can],
  );

  return (
    <aside className={cn("sidebar min-h-0", collapsed ? "w-sidebar-collapsed" : "w-sidebar")}>
      {/* ---------------------------------------------------------------- brand */}
      <div className={cn("sidebar-brand justify-between", collapsed && "justify-center px-0")}>
        <NavLink to="/" onClick={onNavigate} className="rounded-md" aria-label={t("app.name")}>
          <Brand compact={collapsed} />
        </NavLink>
        {collapsed ? null : (
          <button
            type="button"
            onClick={onToggleCollapse}
            className="hidden size-7 items-center justify-center rounded-md text-sidebar-muted transition-colors hover:bg-white/5 hover:text-sidebar-ink lg:flex"
            aria-label={t("nav.collapse")}
            aria-expanded={!collapsed}
            title={t("nav.collapse")}
          >
            <ChevronsLeft className="size-4 rtl:rotate-180" aria-hidden strokeWidth={1.75} />
          </button>
        )}
      </div>

      {/* ---------------------------------------------------------------- navigation */}
      <nav
        className="sidebar-scroll flex-1 overflow-y-auto overflow-x-hidden px-2.5 pb-2"
        aria-label={t("nav.primary")}
      >
        {groups.map((group, index) => (
          <div key={group.id} className="sidebar-group">
            {collapsed ? (
              index === 0 ? (
                <div className="h-2" aria-hidden />
              ) : (
                <div className="sidebar-divider" aria-hidden />
              )
            ) : (
              <p className="sidebar-group-label">{t(group.labelKey)}</p>
            )}
            <ul className="space-y-0.5">
              {group.items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.to === "/"}
                    onClick={onNavigate}
                    aria-label={t(`nav.${item.key}`)}
                    title={collapsed ? t(`nav.${item.key}`) : undefined}
                    className={({ isActive }) =>
                      cn(
                        "sidebar-link",
                        collapsed && "sidebar-link-collapsed",
                        isActive && "sidebar-link-active",
                      )
                    }
                  >
                    <span className="sidebar-icon">
                      <item.icon className="size-[17px]" aria-hidden strokeWidth={1.7} />
                    </span>
                    <span className={cn("truncate", collapsed && "sr-only")}>{t(`nav.${item.key}`)}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      {/* ---------------------------------------------------------------- footer */}
      <div className="shrink-0 border-t border-[color:var(--sidebar-line)] p-2.5">
        {collapsed ? (
          <div className="flex flex-col items-center gap-1.5">
            <button
              type="button"
              onClick={onToggleCollapse}
              className="flex size-9 items-center justify-center rounded-md text-sidebar-muted transition-colors hover:bg-white/5 hover:text-sidebar-ink"
              aria-label={t("nav.expand")}
              aria-expanded={!collapsed}
              title={t("nav.expand")}
            >
              <ChevronsRight className="size-4 rtl:rotate-180" aria-hidden strokeWidth={1.75} />
            </button>
            <button
              type="button"
              onClick={onSignOut}
              className="flex size-9 items-center justify-center rounded-md text-sidebar-muted transition-colors hover:bg-white/5 hover:text-sidebar-ink"
              aria-label={t("common.signOut")}
              title={t("common.signOut")}
            >
              <LogOut className="size-4 rtl:rotate-180" aria-hidden strokeWidth={1.75} />
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            <div className="sidebar-workspace">
              <p className="text-[0.625rem] font-semibold uppercase tracking-[0.18em] text-sidebar-label">
                {t("nav.workspace")}
              </p>
              <p className="mt-0.5 truncate text-[0.8125rem] font-medium text-sidebar-ink" title={tenantName}>
                {tenantName}
              </p>
              <p className="mt-1 flex items-center gap-1.5 text-3xs uppercase tracking-[0.14em] text-sidebar-muted">
                <span className="size-1 rounded-full bg-gold" aria-hidden />
                {plan ? plan.replaceAll("_", " ") : t("nav.plan")}
              </p>
            </div>
            <button
              type="button"
              onClick={onSignOut}
              className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-sidebar-muted transition-colors hover:bg-white/5 hover:text-sidebar-ink"
            >
              <LogOut className="size-[15px] rtl:rotate-180" aria-hidden strokeWidth={1.75} />
              {t("common.signOut")}
            </button>
          </div>
        )}
      </div>
    </aside>
  );
}
