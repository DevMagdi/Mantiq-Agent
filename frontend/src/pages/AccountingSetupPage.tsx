import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Landmark, Plus, Power } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeading } from "@/components/Shell";
import {
  Badge,
  Card,
  CardHeader,
  Drawer,
  EmptyState,
  ErrorState,
  LoadingBlock,
  Spinner,
  Tabs,
  TabList,
  TabPanel,
  useToast,
} from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { PERM } from "@/lib/permissions";
import { useAuth } from "@/lib/session";
import type { AppLang } from "@/i18n";
import type { CostCenter, GlAccount } from "@/lib/types";

const KINDS = ["asset", "liability", "equity", "revenue", "expense"] as const;

export default function AccountingSetupPage() {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language === "ar" ? "ar" : "en") as AppLang;
  const { can } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState("accounts");
  const [accountOpen, setAccountOpen] = useState(false);
  const [centerOpen, setCenterOpen] = useState(false);
  const [account, setAccount] = useState({ code: "", name: "", name_ar: "", kind: "expense" as (typeof KINDS)[number] });
  const [center, setCenter] = useState({ code: "", name: "", name_ar: "" });

  const accounts = useQuery({
    queryKey: ["gl-accounts"],
    queryFn: () => api.get<{ items: GlAccount[] }>("/api/v1/accounting/accounts?include_inactive=true"),
  });
  const centers = useQuery({
    queryKey: ["cost-centers"],
    queryFn: () => api.get<{ items: CostCenter[] }>("/api/v1/accounting/cost-centers?include_inactive=true"),
  });

  const createAccount = useMutation({
    mutationFn: () => api.post("/api/v1/accounting/accounts", account),
    onSuccess: async () => {
      toast.push(t("accounting.setup.toast.accountCreated"), "ok");
      setAccountOpen(false);
      setAccount({ code: "", name: "", name_ar: "", kind: "expense" });
      await queryClient.invalidateQueries({ queryKey: ["gl-accounts"] });
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const createCenter = useMutation({
    mutationFn: () => api.post("/api/v1/accounting/cost-centers", center),
    onSuccess: async () => {
      toast.push(t("accounting.setup.toast.centerCreated"), "ok");
      setCenterOpen(false);
      setCenter({ code: "", name: "", name_ar: "" });
      await queryClient.invalidateQueries({ queryKey: ["cost-centers"] });
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const toggleAccount = useMutation({
    mutationFn: (row: GlAccount) =>
      api.patch(`/api/v1/accounting/accounts/${row.code}`, { is_active: !row.is_active }),
    onSuccess: async () => {
      toast.push(t("accounting.setup.toast.updated"), "ok");
      await queryClient.invalidateQueries({ queryKey: ["gl-accounts"] });
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  const toggleCenter = useMutation({
    mutationFn: (row: CostCenter) =>
      api.patch(`/api/v1/accounting/cost-centers/${row.code}`, { is_active: !row.is_active }),
    onSuccess: async () => {
      toast.push(t("accounting.setup.toast.updated"), "ok");
      await queryClient.invalidateQueries({ queryKey: ["cost-centers"] });
    },
    onError: (error) => toast.push(errorMessage(error), "danger"),
  });

  return (
    <div className="space-y-4">
      <PageHeading
        eyebrow={t("accounting.eyebrow")}
        title={t("accounting.setup.title")}
        subtitle={t("accounting.setup.subtitle")}
        actions={
          tab === "accounts" && can(PERM.accountingWrite) ? (
            <button type="button" className="btn-primary btn-sm" onClick={() => setAccountOpen(true)}>
              <Plus className="size-3.5" aria-hidden />
              {t("accounting.setup.newAccount")}
            </button>
          ) : tab === "centers" && can(PERM.costWrite) ? (
            <button type="button" className="btn-primary btn-sm" onClick={() => setCenterOpen(true)}>
              <Plus className="size-3.5" aria-hidden />
              {t("accounting.setup.newCenter")}
            </button>
          ) : null
        }
      />

      <Card>
        <Tabs value={tab} onValueChange={setTab}>
          <div className="card-header">
            <TabList
              items={[
                { value: "accounts", label: t("accounting.setup.tabs.accounts") },
                { value: "centers", label: t("accounting.setup.tabs.centers") },
              ]}
            />
            <Badge tone="neutral">
              {tab === "accounts"
                ? t("accounting.setup.count", { count: accounts.data?.items.length ?? 0 })
                : t("accounting.setup.count", { count: centers.data?.items.length ?? 0 })}
            </Badge>
          </div>

          <TabPanel value="accounts">
            {accounts.isLoading ? (
              <LoadingBlock />
            ) : accounts.isError ? (
              <ErrorState message={errorMessage(accounts.error)} onRetry={() => void accounts.refetch()} />
            ) : (accounts.data?.items ?? []).length === 0 ? (
              <EmptyState title={t("accounting.setup.emptyAccounts")} icon={<Landmark className="size-5" />} />
            ) : (
              <div className="table-scroll overflow-x-auto">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t("accounting.reports.columns.code")}</th>
                      <th>{t("accounting.reports.columns.account")}</th>
                      <th>{t("accounting.reports.columns.kind")}</th>
                      <th>{t("accounting.setup.normalBalance")}</th>
                      <th>{t("accounting.setup.control")}</th>
                      <th>{t("accounting.setup.state")}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {(accounts.data?.items ?? []).map((row) => (
                      <tr key={row.code}>
                        <td className="tabular font-medium text-ink">{row.code}</td>
                        <td>
                          <span className="block text-ink">{lang === "ar" && row.name_ar ? row.name_ar : row.name}</span>
                          {row.description ? <span className="block text-2xs text-ink-subtle">{row.description}</span> : null}
                        </td>
                        <td className="text-ink-muted">{t(`accounting.kind.${row.kind}`)}</td>
                        <td className="text-ink-muted">{t(`accounting.setup.balance.${row.normal_balance}`)}</td>
                        <td>{row.is_control ? <Badge tone="gold">{t("accounting.setup.controlBadge")}</Badge> : <span className="text-ink-subtle">—</span>}</td>
                        <td>
                          <Badge tone={row.is_active ? "ok" : "neutral"}>
                            {row.is_active ? t("accounting.setup.active") : t("accounting.setup.inactive")}
                          </Badge>
                        </td>
                        <td className="text-end">
                          {can(PERM.accountingWrite) ? (
                            <button type="button" className="btn-ghost btn-sm"
                                    aria-label={row.is_active ? t("accounting.setup.deactivate") : t("accounting.setup.activate")}
                                    disabled={toggleAccount.isPending}
                                    onClick={() => toggleAccount.mutate(row)}>
                              <Power className="size-3.5" aria-hidden />
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </TabPanel>

          <TabPanel value="centers">
            {centers.isLoading ? (
              <LoadingBlock />
            ) : centers.isError ? (
              <ErrorState message={errorMessage(centers.error)} onRetry={() => void centers.refetch()} />
            ) : (centers.data?.items ?? []).length === 0 ? (
              <EmptyState title={t("accounting.setup.emptyCenters")} icon={<Building2 className="size-5" />} />
            ) : (
              <div className="table-scroll overflow-x-auto">
                <table className="table">
                  <thead>
                    <tr>
                      <th>{t("accounting.reports.columns.code")}</th>
                      <th>{t("accounting.setup.centerName")}</th>
                      <th>{t("accounting.setup.state")}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {(centers.data?.items ?? []).map((row) => (
                      <tr key={row.code}>
                        <td className="tabular font-medium text-ink">{row.code}</td>
                        <td className="text-ink">{lang === "ar" && row.name_ar ? row.name_ar : row.name}</td>
                        <td>
                          <Badge tone={row.is_active ? "ok" : "neutral"}>
                            {row.is_active ? t("accounting.setup.active") : t("accounting.setup.inactive")}
                          </Badge>
                        </td>
                        <td className="text-end">
                          {can(PERM.costWrite) ? (
                            <button type="button" className="btn-ghost btn-sm"
                                    aria-label={row.is_active ? t("accounting.setup.deactivate") : t("accounting.setup.activate")}
                                    disabled={toggleCenter.isPending}
                                    onClick={() => toggleCenter.mutate(row)}>
                              <Power className="size-3.5" aria-hidden />
                            </button>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </TabPanel>
        </Tabs>
      </Card>

      <Card>
        <CardHeader title={t("accounting.setup.dimensionsTitle")} subtitle={t("accounting.setup.dimensionsBody")} />
      </Card>

      <Drawer
        open={accountOpen}
        onClose={() => setAccountOpen(false)}
        title={t("accounting.setup.newAccount")}
        description={t("accounting.setup.newAccountHint")}
        footer={
          <button type="button" className="btn-primary btn-sm w-full"
                  disabled={createAccount.isPending || account.code.trim().length < 3 || account.name.trim().length < 2}
                  onClick={() => createAccount.mutate()}>
            {createAccount.isPending ? <Spinner /> : <Plus className="size-3.5" aria-hidden />}
            {t("accounting.setup.add")}
          </button>
        }
      >
        <div className="space-y-3">
          <label className="block">
            <span className="eyebrow">{t("accounting.reports.columns.code")}</span>
            <input className="input mt-1 w-full tabular" value={account.code}
                   onChange={(event) => setAccount((row) => ({ ...row, code: event.target.value }))} />
          </label>
          <label className="block">
            <span className="eyebrow">{t("accounting.setup.nameEn")}</span>
            <input className="input mt-1 w-full" value={account.name}
                   onChange={(event) => setAccount((row) => ({ ...row, name: event.target.value }))} />
          </label>
          <label className="block">
            <span className="eyebrow">{t("accounting.setup.nameAr")}</span>
            <input className="input mt-1 w-full" dir="rtl" value={account.name_ar}
                   onChange={(event) => setAccount((row) => ({ ...row, name_ar: event.target.value }))} />
          </label>
          <label className="block">
            <span className="eyebrow">{t("accounting.reports.columns.kind")}</span>
            <select className="input mt-1 w-full" value={account.kind}
                    onChange={(event) => setAccount((row) => ({ ...row, kind: event.target.value as (typeof KINDS)[number] }))}>
              {KINDS.map((kind) => (
                <option key={kind} value={kind}>{t(`accounting.kind.${kind}`)}</option>
              ))}
            </select>
          </label>
        </div>
      </Drawer>

      <Drawer
        open={centerOpen}
        onClose={() => setCenterOpen(false)}
        title={t("accounting.setup.newCenter")}
        description={t("accounting.setup.newCenterHint")}
        footer={
          <button type="button" className="btn-primary btn-sm w-full"
                  disabled={createCenter.isPending || center.code.trim().length < 2 || center.name.trim().length < 2}
                  onClick={() => createCenter.mutate()}>
            {createCenter.isPending ? <Spinner /> : <Plus className="size-3.5" aria-hidden />}
            {t("accounting.setup.add")}
          </button>
        }
      >
        <div className="space-y-3">
          <label className="block">
            <span className="eyebrow">{t("accounting.reports.columns.code")}</span>
            <input className="input mt-1 w-full tabular" value={center.code}
                   onChange={(event) => setCenter((row) => ({ ...row, code: event.target.value }))} />
          </label>
          <label className="block">
            <span className="eyebrow">{t("accounting.setup.nameEn")}</span>
            <input className="input mt-1 w-full" value={center.name}
                   onChange={(event) => setCenter((row) => ({ ...row, name: event.target.value }))} />
          </label>
          <label className="block">
            <span className="eyebrow">{t("accounting.setup.nameAr")}</span>
            <input className="input mt-1 w-full" dir="rtl" value={center.name_ar}
                   onChange={(event) => setCenter((row) => ({ ...row, name_ar: event.target.value }))} />
          </label>
        </div>
      </Drawer>
    </div>
  );
}
