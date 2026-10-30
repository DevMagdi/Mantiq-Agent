import {
  Activity,
  BarChart3,
  Bell,
  CheckSquare,
  ClipboardList,
  Factory,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Settings,
  Sun,
  Truck,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/session";
import { useRealtime } from "@/lib/realtime";
import { dateTime } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { Notification, Page } from "@/lib/types";
import { Avatar, Badge, cn, EmptyState } from "./ui";
import { Brand } from "./Brand";

const NAV = [
  { to: "/", key: "dashboard", icon: LayoutDashboard, permission: "report:read" },
  { to: "/requests", key: "requests", icon: ClipboardList, permission: "pr:read" },
  { to: "/rfqs", key: "rfqs", icon: Factory, permission: "rfq:read" },
  { to: "/approvals", key: "approvals", icon: CheckSquare, permission: "approval:read" },
  { to: "/suppliers", key: "suppliers", icon: Truck, permission: "supplier:read" },
  { to: "/analytics", key: "analytics", icon: BarChart3, permission: "report:read" },
  { to: "/activity", key: "agents", icon: Activity, permission: "report:read" },
  { to: "/settings", key: "settings", icon: Settings, permission: "settings:read" },
] as const;

export function Shell() {
  const { t, i18n } = useTranslation();
  const { user, logout, can, theme, setTheme } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);

  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;

  const notifications = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get<Page<Notification>>("/api/v1/notifications?unread_only=false&limit=20"),
    refetchInterval: 60_000,
  });

  const unread = useMemo(
    () => (notifications.data?.items ?? []).filter((row) => !row.read_at).length,
    [notifications.data],
  );

  const connected = useRealtime((event) => {
    if (event.type === "notification" || event.type === "approval.requested" || event.type === "reply.received") {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    }
  });

  useEffect(() => {
    setMobileOpen(false);
    setBellOpen(false);
  }, [location.pathname]);

  const items = NAV.filter((item) => can(item.permission));

  const markAllRead = async () => {
    await api.post("/api/v1/notifications/read-all");
    await queryClient.invalidateQueries({ queryKey: ["notifications"] });
  };

  const changeLang = (next: AppLang) => {
    void i18n.changeLanguage(next);
    document.documentElement.lang = next;
    document.documentElement.dir = next === "ar" ? "rtl" : "ltr";
    localStorage.setItem("procureai.lang", next);
  };

  return (
    <div className="app-shell bg-surface">
      <aside
        className={cn(
          "fixed inset-y-0 z-30 flex w-64 shrink-0 flex-col border-e border-line bg-[color:var(--navy-900)]",
          "transition-transform lg:static lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "ltr:-translate-x-full rtl:translate-x-full lg:translate-x-0",
        )}
        aria-label={t("app.name")}
      >
        <div className="flex items-center justify-between px-4 py-4">
          <Brand />
          <button type="button" className="btn-ghost lg:hidden" onClick={() => setMobileOpen(false)} aria-label="Close menu">
            <X className="size-4" />
          </button>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto px-2 pb-4">
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-gold/15 text-[color:var(--gold-300)]"
                    : "text-[color:var(--ink-muted)] hover:bg-white/5 hover:text-ink",
                )
              }
            >
              <item.icon className="size-4" aria-hidden />
              {t(`nav.${item.key}`)}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-white/10 px-4 py-3 text-2xs text-[color:var(--ink-subtle)]">
          <p>{user?.tenant.name}</p>
          <p className="mt-0.5 uppercase tracking-widest">{user?.tenant.plan}</p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b border-line bg-surface/90 px-3 backdrop-blur lg:px-6">
          <button
            type="button"
            className="btn-ghost lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open menu"
          >
            <Menu className="size-4" />
          </button>
          <div className="flex-1" />
          <span
            title={connected ? "Live" : "Polling"}
            className={cn(
              "hidden items-center gap-1.5 text-2xs sm:flex",
              connected ? "text-[color:var(--ok)]" : "text-ink-subtle",
            )}
          >
            <span className={cn("size-1.5 rounded-full", connected ? "bg-[color:var(--ok)]" : "bg-line")} />
            {connected ? "LIVE" : "POLL"}
          </span>
          <div className="relative">
            <button
              type="button"
              className="btn-ghost relative"
              onClick={() => setBellOpen((open) => !open)}
              aria-label={t("notifications.title")}
              aria-expanded={bellOpen}
            >
              <Bell className="size-4" />
              {unread > 0 ? (
                <span className="absolute -top-1 end-1 flex size-4 items-center justify-center rounded-full bg-gold text-[10px] font-semibold text-[color:var(--gold-contrast)]">
                  {unread > 9 ? "9+" : unread}
                </span>
              ) : null}
            </button>
            {bellOpen ? (
              <div className="absolute end-0 top-11 z-30 w-80 card p-0 shadow-pop animate-slideIn">
                <div className="flex items-center justify-between border-b border-line px-3 py-2">
                  <p className="text-sm font-medium">{t("notifications.title")}</p>
                  <button type="button" className="text-2xs text-gold hover:underline" onClick={() => void markAllRead()}>
                    {t("notifications.markAll")}
                  </button>
                </div>
                <div className="max-h-80 overflow-y-auto">
                  {notifications.isLoading ? (
                    <p className="px-3 py-6 text-center text-xs text-ink-muted">{t("common.loading")}</p>
                  ) : (notifications.data?.items.length ?? 0) === 0 ? (
                    <EmptyState title={t("notifications.empty")} />
                  ) : (
                    notifications.data?.items.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className="flex w-full flex-col gap-1 border-b border-line/60 px-3 py-2 text-start hover:bg-surface-raised"
                        onClick={async () => {
                          await api.post(`/api/v1/notifications/${item.id}/read`);
                          await queryClient.invalidateQueries({ queryKey: ["notifications"] });
                          if (item.link) navigate(item.link);
                        }}
                      >
                        <span className="flex items-center gap-2 text-sm text-ink">
                          {!item.read_at ? <span className="size-1.5 rounded-full bg-gold" aria-hidden /> : null}
                          {lang === "ar" ? item.title_ar || item.title : item.title}
                        </span>
                        <span className="text-2xs text-ink-muted">{dateTime(item.created_at, lang)}</span>
                      </button>
                    ))
                  )}
                </div>
              </div>
            ) : null}
          </div>
          <button type="button" className="btn-ghost" onClick={() => changeLang(lang === "ar" ? "en" : "ar")}>
            {lang === "ar" ? "EN" : "ع"}
          </button>
          <button
            type="button"
            className="btn-ghost"
            aria-label={t("common.theme")}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </button>
          <div className="ms-1 flex items-center gap-2 border-s border-line ps-2">
            <Avatar name={user?.full_name ?? "?"} />
            <div className="hidden leading-tight sm:block">
              <p className="text-xs font-medium text-ink">{lang === "ar" && user?.full_name_ar ? user.full_name_ar : user?.full_name}</p>
              <p className="text-2xs text-ink-subtle">{user?.roles.join(" · ")}</p>
            </div>
            <button
              type="button"
              className="btn-ghost"
              aria-label={t("common.signOut")}
              onClick={() => {
                void logout().then(() => navigate("/login"));
              }}
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

export function PageHeading({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-ink">{title}</h1>
        {subtitle ? <p className="mt-1 text-sm text-ink-muted">{subtitle}</p> : null}
        {children}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function LiveBadge({ label }: { label: string }) {
  return <Badge tone="ok">{label}</Badge>;
}
