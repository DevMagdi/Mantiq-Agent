import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import "../i18n";
import { Sidebar } from "./Sidebar";

const ALL = [
  "report:read", "pr:read", "rfq:read", "approval:read", "po:read",
  "supplier:read", "audit:read", "settings:read",
];

function renderRail(path = "/rfqs", permissions = ALL, collapsed = false) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar
        can={(permission) => permissions.includes(permission)}
        tenantName="Nile Manufacturing Group"
        plan="growth"
        onSignOut={vi.fn()}
        collapsed={collapsed}
        onToggleCollapse={vi.fn()}
      />
    </MemoryRouter>,
  );
}

describe("the rail", () => {
  it("groups the product into buy / receive / measure and marks the current screen", () => {
    renderRail("/rfqs");
    const nav = screen.getByRole("navigation", { name: "التنقل الرئيسي" });
    for (const label of ["نظرة عامة", "التوريد", "التنفيذ والاستلام", "التحليل"]) {
      expect(within(nav).getByText(label)).toBeTruthy();
    }
    const current = within(nav).getByRole("link", { name: "طلبات عروض الأسعار" });
    expect(current.getAttribute("aria-current")).toBe("page");
    expect(current.className).toContain("sidebar-link-active");
  });

  it("renders only the destinations the role may open", () => {
    renderRail("/", ["pr:read"]);
    const nav = screen.getByRole("navigation");
    expect(within(nav).getByRole("link", { name: "طلبات الشراء" })).toBeTruthy();
    expect(within(nav).queryByRole("link", { name: "الاعتمادات" })).toBeNull();
    expect(within(nav).queryByRole("link", { name: "نشاط الوكلاء" })).toBeNull();
  });

  it("keeps every label accessible when collapsed", () => {
    renderRail("/rfqs", ALL, true);
    // Icons only, but the accessible name survives and the tooltip carries it too.
    const link = screen.getByRole("link", { name: "طلبات عروض الأسعار" });
    expect(link.getAttribute("title")).toBe("طلبات عروض الأسعار");
    expect(link.className).toContain("sidebar-link-collapsed");
    // The collapsed footer exposes expand + sign out as icon buttons with names.
    expect(screen.getByRole("button", { name: "توسيع الشريط الجانبي" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "تسجيل الخروج" })).toBeTruthy();
  });

  it("shows the workspace card and a single sign-out action when expanded", () => {
    renderRail();
    expect(screen.getByText("مساحة العمل")).toBeTruthy();
    expect(screen.getByText("Nile Manufacturing Group")).toBeTruthy();
    expect(screen.getByText("growth")).toBeTruthy();
    expect(screen.getByRole("button", { name: "تسجيل الخروج" })).toBeTruthy();
    // Collapse lives in the brand row so the footer stays a workspace card.
    expect(screen.getByRole("button", { name: "طيّ الشريط الجانبي" })).toBeTruthy();
  });
});
