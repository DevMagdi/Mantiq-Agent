import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "./i18n";

/**
 * Accounting is a *deployment* switch, not a permission: with `ACCOUNTING_ENABLED=false` the API does
 * not mount the module at all, and the backend strips its permissions from every caller. This test
 * signs in as a controller (the role that owns the books) with exactly the capability set the API
 * would return in that mode, and asserts that the pilot build leaves no trace:
 *
 * * no accounting group and no accounting item in the sidebar;
 * * `/accounting`, `/accounting/reports` and `/accounting/setup` render the normal 404 page;
 * * the pages never even request an accounting endpoint (so the console stays clean);
 * * a purchase order still opens, without postings, the audit package or the lifecycle rail.
 */

const CONTROLLER_PERMISSIONS = [
  "pr:read", "rfq:read", "supplier:read", "offer:read", "comparison:read", "comment:create",
  "report:read", "settings:read", "po:read", "po:update", "po:export", "receipt:create",
  "invoice:create", "invoice:approve", "payment:record", "approval:read", "approval:decide",
  "audit:read", "channel:read", "inbox:read", "inbox:reply", "assistant:use",
  // deliberately absent: accounting:*, cost:*, ledger:read, audit_package:*
];

const ACCOUNTING_PATHS = [
  "/api/v1/accounting",
  "/api/v1/supplier-balances",
  "/lifecycle",
  "/audit-package",
  "/accounting",
  "/posting",
];

const { apiMock } = vi.hoisted(() => ({
  apiMock: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    download: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  api: apiMock,
  errorMessage: (error: unknown) => (error instanceof Error ? error.message : "error"),
  session: { get: () => "jwt", set: () => undefined, clear: () => undefined },
}));

vi.mock("@/lib/session", async () => {
  const actual = await vi.importActual<typeof import("@/lib/session")>("@/lib/session");
  return {
    ...actual,
    useAuth: () => ({
      user: {
        id: "u1",
        email: "controller@nile.example.com",
        full_name: "Nour Hassan",
        roles: ["controller"],
        permissions: CONTROLLER_PERMISSIONS,
        tenant: { name: "Nile", slug: "nile", plan: "growth" },
      },
      status: "authenticated",
      challenge: null,
      login: vi.fn(),
      verifyMfa: vi.fn(),
      logout: vi.fn(),
      refreshUser: vi.fn(),
      can: (permission: string) => CONTROLLER_PERMISSIONS.includes(permission),
    }),
  };
});

vi.mock("@/lib/realtime", () => ({ useRealtime: () => true }));

import App from "./App";
import { ToastProvider } from "./components/ui";

const PO_DETAIL = {
  id: "po-1",
  code: "PO-2026-00001",
  status: "issued",
  status_label: "Issued",
  status_label_ar: "صادر",
  amount: "12000.00",
  currency: "EGP",
  supplier: { id: "s1", name: "Nile Steel", name_ar: "حديد النيل", status: "approved" },
  rfq: { id: "r1", code: "RFQ-2026-00001", title: "Rebar", payment_terms: "Net 30", incoterm: "DAP" },
  lines: [{ line_no: 1, description: "Rebar 16mm", quantity: "12.00", uom: "t",
            unit_price: "1000.00", line_total: "12000.00" }],
  timeline: [],
  receipts: [],
  invoices: [],
  discrepancy: { lines: [], ordered_total: "12.00", received_total: "0.00",
                 outstanding_lines: 1, short_delivery: false },
  supplier_balance: null,
  next_status: "in_transit",
  can_close: false,
  document_html: "<html></html>",
};

const RESPONSES: Record<string, unknown> = {
  "/api/v1/notifications": { items: [], unread: 0 },
  "/api/v1/purchase-orders/po-1": PO_DETAIL,
  "/api/v1/purchase-orders": { items: [], total: 0, page: 1, page_size: 100 },
  "/api/v1/meta/capabilities": { adapters: {}, features: {} },
};

function renderAt(path: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <ToastProvider>
          <App />
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function calledPaths(): string[] {
  return [...apiMock.get.mock.calls, ...apiMock.post.mock.calls].map(([path]) => String(path));
}

describe("accounting is invisible when the deployment switches it off", () => {
  beforeEach(() => {
    apiMock.get.mockReset();
    apiMock.post.mockReset();
    apiMock.get.mockImplementation((path: string) => {
      const key = Object.keys(RESPONSES).find((prefix) => String(path).startsWith(prefix));
      return Promise.resolve(key ? RESPONSES[key] : { items: [], total: 0 });
    });
    apiMock.post.mockResolvedValue({ ok: true });
  });

  it("shows no accounting group or item in the sidebar", async () => {
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: "التنقل الرئيسي" });
    const labels = Array.from(nav.querySelectorAll("a")).map((link) => link.textContent ?? "");
    expect(labels.length).toBeGreaterThan(0);
    for (const marker of ["اليومية", "التكاليف", "إعداد المحاسبة", "المحاسبة"]) {
      expect(labels.some((label) => label.includes(marker))).toBe(false);
    }
    // The rest of the controller's navigation is intact.
    expect(labels.some((label) => label.includes("أوامر الشراء"))).toBe(true);
  });

  for (const path of ["/accounting", "/accounting/reports", "/accounting/setup"]) {
    it(`renders the normal 404 page at ${path}`, async () => {
      renderAt(path);
      expect(await screen.findByText("404")).toBeTruthy();
      // Not a permission panel, not a placeholder: an unknown URL.
      expect(screen.queryByText("ليست لديك صلاحية الوصول إلى هذه الشاشة")).toBeNull();
      expect(calledPaths().some((p) => ACCOUNTING_PATHS.some((needle) => p.includes(needle)))).toBe(false);
    });
  }

  it("opens a purchase order without postings, audit package or the accounting rail", async () => {
    renderAt("/purchase-orders/po-1");
    await waitFor(() => expect(apiMock.get).toHaveBeenCalledWith("/api/v1/purchase-orders/po-1"));
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(calledPaths().filter((p) => p.includes("/lifecycle"))).toEqual([]);
    expect(calledPaths().filter((p) => p.includes("/audit-package"))).toEqual([]);
    expect(calledPaths().filter((p) => p.includes("/postings"))).toEqual([]);
    expect(await screen.findByText("PO-2026-00001")).toBeTruthy();
  });
});
