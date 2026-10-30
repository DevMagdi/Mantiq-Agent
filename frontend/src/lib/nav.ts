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

export type NavItem = { to: string; key: string; icon: LucideIcon; permission: string };
export type NavGroup = { id: string; labelKey: string; items: NavItem[] };

/** Grouped so a newcomer reads the product as three jobs: buy, receive, measure. */
export const NAV_GROUPS: NavGroup[] = [
  {
    id: "overview",
    labelKey: "nav.groups.overview",
    items: [
      { to: "/", key: "dashboard", icon: LayoutDashboard, permission: "report:read" },
      { to: "/requests", key: "requests", icon: ClipboardList, permission: "pr:read" },
    ],
  },
  {
    id: "sourcing",
    labelKey: "nav.groups.sourcing",
    items: [
      { to: "/rfqs", key: "rfqs", icon: Factory, permission: "rfq:read" },
      { to: "/approvals", key: "approvals", icon: CheckSquare, permission: "approval:read" },
    ],
  },
  {
    id: "fulfilment",
    labelKey: "nav.groups.fulfilment",
    items: [
      { to: "/purchase-orders", key: "purchaseOrders", icon: PackageCheck, permission: "po:read" },
      { to: "/suppliers", key: "suppliers", icon: Truck, permission: "supplier:read" },
    ],
  },
  {
    id: "insight",
    labelKey: "nav.groups.insight",
    items: [
      { to: "/analytics", key: "analytics", icon: BarChart3, permission: "report:read" },
      { to: "/activity", key: "agents", icon: Activity, permission: "report:read" },
      { to: "/settings", key: "settings", icon: Settings, permission: "settings:read" },
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
