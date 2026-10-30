import {
  Activity,
  BarChart3,
  CheckSquare,
  ClipboardList,
  Factory,
  LayoutDashboard,
  PackageCheck,
  Settings,
  Truck,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { PERM } from "./permissions";

export type NavItem = { to: string; key: string; icon: LucideIcon; permission: string };
export type NavGroup = { id: string; labelKey: string; items: NavItem[] };

/** Grouped so a newcomer reads the product as three jobs: buy, receive, measure. */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: "overview",
    labelKey: "nav.groups.overview",
    items: [
      { to: "/", key: "dashboard", icon: LayoutDashboard, permission: PERM.reportRead },
      { to: "/requests", key: "requests", icon: ClipboardList, permission: PERM.prRead },
    ],
  },
  {
    id: "sourcing",
    labelKey: "nav.groups.sourcing",
    items: [
      { to: "/rfqs", key: "rfqs", icon: Factory, permission: PERM.rfqRead },
      { to: "/approvals", key: "approvals", icon: CheckSquare, permission: PERM.approvalRead },
    ],
  },
  {
    id: "fulfilment",
    labelKey: "nav.groups.fulfilment",
    items: [
      { to: "/purchase-orders", key: "purchaseOrders", icon: PackageCheck, permission: PERM.poRead },
      { to: "/suppliers", key: "suppliers", icon: Truck, permission: PERM.supplierRead },
    ],
  },
  {
    id: "insight",
    labelKey: "nav.groups.insight",
    items: [
      { to: "/analytics", key: "analytics", icon: BarChart3, permission: PERM.reportRead },
      { to: "/activity", key: "agents", icon: Activity, permission: PERM.auditRead },
      { to: "/settings", key: "settings", icon: Settings, permission: PERM.settingsRead },
    ],
  },
];

const COLLAPSE_KEY = "procureai.sidebar";

/** Collapsed state persists locally; it is a UI preference, not a tenant setting. */
export function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === "collapsed");
  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? "collapsed" : "expanded");
  }, [collapsed]);
  return { collapsed, toggle: () => setCollapsed((value) => !value) };
}
