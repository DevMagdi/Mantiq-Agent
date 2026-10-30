import { ChevronLeft, ChevronRight, LogOut } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { NavLink } from "react-router-dom";
import { NAV_GROUPS } from "@/lib/nav";
import { cn } from "./ui";
import { Brand } from "./Brand";

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
    () => NAV_GROUPS.map((group) => ({ ...group, items: group.items.filter((item) => can(item.permission)) }))
      .filter((group) => group.items.length > 0),
    [can],
  );

  return (
    <aside className={cn("sidebar min-h-0", collapsed ? "w-sidebar-collapsed" : "w-sidebar")} aria-label={t("nav.primary")}>
      <div className={cn("flex h-header items-center gap-2 border-b border-sidebar-line px-3", collapsed && "justify-center px-0")}>
        <Brand compact={collapsed} />
      </div>

      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 pb-3">
        {groups.map((group) => (
          <div key={group.id}>
            {collapsed ? (
              <div className="mx-2 my-3 h-px bg-sidebar-line" aria-hidden />
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
                        collapsed ? "min-h-10 justify-center px-0 py-2.5" : "px-3",
                        isActive && "sidebar-link-active",
                      )
                    }
                  >
                    <item.icon className="size-[18px] shrink-0" aria-hidden strokeWidth={1.75} />
                    <span className={cn("truncate", collapsed && "sr-only")}>{t(`nav.${item.key}`)}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-sidebar-line p-2">
        {collapsed ? null : (
          <div className="mb-2 rounded-md bg-sidebar-raised/60 px-3 py-2">
            <p className="truncate text-xs font-medium text-sidebar-ink">{tenantName}</p>
            <p className="mt-0.5 text-3xs uppercase tracking-[0.16em] text-sidebar-muted">{plan}</p>
          </div>
        )}
        <div className={cn("flex items-center", collapsed ? "flex-col gap-1" : "justify-between")}>
          <button
            type="button"
            className="btn-ghost btn-sm text-sidebar-muted hover:text-sidebar-ink"
            onClick={onToggleCollapse}
            aria-expanded={!collapsed}
            aria-label={collapsed ? t("nav.expand") : t("nav.collapse")}
            title={collapsed ? t("nav.expand") : t("nav.collapse")}
          >
            {collapsed ? (
              <ChevronRight className="size-4 rtl:rotate-180" aria-hidden />
            ) : (
              <ChevronLeft className="size-4 rtl:rotate-180" aria-hidden />
            )}
            {collapsed ? null : <span className="text-2xs">{t("nav.collapse")}</span>}
          </button>
          <button
            type="button"
            className="btn-ghost btn-sm text-sidebar-muted hover:text-sidebar-ink"
            onClick={onSignOut}
            aria-label={t("common.signOut")}
            title={t("common.signOut")}
          >
            <LogOut className="size-4" aria-hidden />
            {collapsed ? null : <span className="text-2xs">{t("common.signOut")}</span>}
          </button>
        </div>
      </div>
    </aside>
  );
}
