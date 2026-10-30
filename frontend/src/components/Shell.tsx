import { Bell, Menu } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/session";
import { useRealtime } from "@/lib/realtime";
import { dateTime } from "@/lib/format";
import type { AppLang } from "@/i18n";
import type { NotificationFeed } from "@/lib/types";
import { Avatar, Badge, cn, EmptyState } from "./ui";
import { Sidebar } from "./Sidebar";
import { useSidebarCollapsed } from "@/lib/nav";

export function Shell() {
  const { t, i18n } = useTranslation();
  const { user, logout, can } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  const { collapsed, toggle } = useSidebarCollapsed();

  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;

  const notifications = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api.get<NotificationFeed>("/api/v1/notifications?unread_only=false"),
    refetchInterval: 60_000,
  });

  // The endpoint returns the unread count for the tenant, not just the page of rows it just sent.
  const unread = notifications.data?.unread ?? 0;

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

  const sidebar = (
    <Sidebar
      can={can}
      tenantName={user?.tenant.name ?? ""}
      plan={user?.tenant.plan ?? ""}
      collapsed={collapsed}
      onToggleCollapse={toggle}
      onNavigate={() => setMobileOpen(false)}
      onSignOut={() => {
        void logout().then(() => navigate("/login"));
      }}
    />
  );

  return (
    <div className="app-shell">
      {/* Desktop rail (static) */}
      <div className="sticky top-0 hidden h-screen shrink-0 lg:block">{sidebar}</div>

      {/* Mobile drawer */}
      {mobileOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-fadeIn"
            aria-label={t("common.close")}
            onClick={() => setMobileOpen(false)}
          />
          <div className="relative h-full w-sidebar animate-slideIn">{sidebar}</div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-header items-center gap-2 border-b border-line bg-surface/85 px-3 backdrop-blur-md lg:px-5">
          <button
            type="button"
            className="btn-ghost btn-sm lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label={t("nav.open")}
          >
            <Menu className="size-4" />
          </button>

          <div className="flex-1" />

          <span
            title={connected ? t("common.live") : t("common.polling")}
            className={cn(
              "hidden items-center gap-1.5 text-3xs font-medium uppercase tracking-[0.14em] sm:flex",
              connected ? "text-[color:var(--ok-ink)]" : "text-ink-subtle",
            )}
          >
            <span className={cn("size-1.5 rounded-full", connected ? "bg-[color:var(--ok)]" : "bg-line")} />
            {connected ? t("common.live") : t("common.polling")}
          </span>

          <div className="relative">
            <button
              type="button"
              className="btn-ghost btn-sm relative"
              onClick={() => setBellOpen((open) => !open)}
              aria-label={t("notifications.title")}
              aria-expanded={bellOpen}
            >
              <Bell className="size-4" />
              {unread > 0 ? (
                <span className="absolute -top-0.5 end-1 flex size-4 items-center justify-center rounded-full bg-gold text-[10px] font-semibold text-[color:var(--gold-contrast)]">
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
                        className="flex w-full flex-col gap-1 border-b border-line/60 px-3 py-2 text-start transition-colors hover:bg-surface-raised"
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

          <button
            type="button"
            className="btn-ghost btn-sm text-2xs font-semibold uppercase tracking-wider"
            onClick={() => changeLang(lang === "ar" ? "en" : "ar")}
            aria-label={t("common.language")}
            title={t("common.language")}
          >
            {lang === "ar" ? "EN" : "ع"}
          </button>

          <div className="ms-1 flex items-center gap-2 border-s border-line ps-2">
            <Avatar name={user?.full_name ?? "?"} />
            <div className="hidden leading-tight sm:block">
              <p className="text-xs font-medium text-ink">
                {lang === "ar" && user?.full_name_ar ? user.full_name_ar : user?.full_name}
              </p>
              <p className="text-3xs uppercase tracking-[0.12em] text-ink-subtle">{user?.roles.join(" · ")}</p>
            </div>
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto px-3 py-4 lg:px-6 lg:py-6">
          <div className="mx-auto w-full max-w-[1440px]">
            <Outlet />
          </div>
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
  eyebrow,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children?: React.ReactNode;
  eyebrow?: string;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {eyebrow ? <p className="eyebrow mb-1">{eyebrow}</p> : null}
        <h1 className="truncate text-[1.375rem] font-semibold leading-tight text-ink">{title}</h1>
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
