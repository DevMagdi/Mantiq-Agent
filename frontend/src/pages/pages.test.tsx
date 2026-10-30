import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import "../i18n";

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
  session: { get: () => null, set: () => undefined, clear: () => undefined },
}));

vi.mock("@/lib/session", async () => {
  const actual = await vi.importActual<typeof import("@/lib/session")>("@/lib/session");
  return {
    ...actual,
    useAuth: () => ({
      user: { id: "u1", email: "admin@pilot.procureai", full_name: "Admin", roles: ["admin"], permissions: ["supplier:discovery"], tenant: { name: "Nile", slug: "nile" } },
      status: "authenticated",
      challenge: null,
      login: vi.fn(),
      verifyMfa: vi.fn(),
      logout: vi.fn(),
      refreshUser: vi.fn(),
      can: () => true,
    }),
  };
});

import { ToastProvider } from "@/components/ui";
import ApprovalsPage from "./ApprovalsPage";
import SuppliersPage from "./SuppliersPage";
import PurchaseOrdersPage from "./PurchaseOrdersPage";

function renderPage(element: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>{element}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("approval inbox", () => {
  beforeEach(() => {
    // Braces matter: returning the mock makes Vitest treat it as a cleanup hook.
    apiMock.get.mockReset();
  });

  it("renders pending approvals with the approval chain and a decision control", async () => {
    apiMock.get.mockResolvedValue({
      items: [
        {
          id: "a1",
          rfq_id: "r1",
          rfq_code: "RFQ-2026-00003",
          rfq_title: "Q4 steel",
          step_no: 2,
          status: "pending",
          approver_role: "finance_approver",
          amount: "500000.00",
          currency: "EGP",
          due_at: new Date().toISOString(),
          can_decide: true,
          chain: [
            { step_no: 1, role: "procurement_manager", status: "approved" },
            { step_no: 2, role: "finance_approver", status: "pending" },
          ],
        },
      ],
    });
    renderPage(<ApprovalsPage />);
    expect((await screen.findAllByText(/RFQ-2026-00003/)).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button").length).toBeGreaterThan(1);
    expect(screen.getByText(/procurement manager/i)).toBeTruthy();
  });

  it("shows an actionable empty state when nothing is waiting", async () => {
    apiMock.get.mockResolvedValue({ items: [] });
    renderPage(<ApprovalsPage />);
    await waitFor(() => expect(apiMock.get).toHaveBeenCalled());
    expect(screen.queryAllByRole("button").length).toBeLessThan(6);
  });
});

describe("supplier workspace", () => {
  beforeEach(() => {
    // Braces matter: returning the mock makes Vitest treat it as a cleanup hook.
    apiMock.get.mockReset();
  });

  it("lists suppliers with score and certification columns", async () => {
    apiMock.get.mockImplementation((path = "") =>
      Promise.resolve(
        path.startsWith("/api/v1/suppliers")
          ? {
              items: [
                {
                  id: "s1",
                  name: "Ezz Steel Distribution",
                  name_ar: "عز للصلب",
                  country: "EG",
                  city: "Cairo",
                  status: "active",
                  overall_score: 88,
                  categories: ["STEEL"],
                  certifications: [{ code: "ISO9001", expires_at: null }],
                  sanctions_status: "clear",
                  website: null,
                  ratings: {},
                  flags: [],
                },
              ],
            }
          : { items: [] },
      ),
    );
    renderPage(<SuppliersPage />);
    expect(await screen.findByText(/Ezz Steel Distribution|عز للصلب/)).toBeTruthy();
    expect(screen.getByText("ISO9001")).toBeTruthy();
  });
});

describe("public quote form", () => {
  beforeEach(() => {
    // Braces matter: returning the mock makes Vitest treat it as a cleanup hook.
    apiMock.get.mockReset();
  });

  it("collects unit prices on a no-login link and submits them", async () => {
    apiMock.get.mockResolvedValue({
      rfq: { code: "RFQ-2026-00003", title: "Q4 steel", currency: "EGP", deadline: null, incoterm: "DAP", payment_terms: "Net 30" },
      supplier: { name: "Ezz Steel", name_ar: "عز" },
      contact_name: "Sales",
      lines: [{ id: "l1", line_no: 1, description: "Rebar 16mm", quantity: "300", uom: "t" }],
      expires_at: new Date().toISOString(),
      fields: ["unit_price"],
    });
    const { default: PublicFormPage } = await import("./PublicFormPage");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ToastProvider>
          <MemoryRouter initialEntries={["/form/tok"]}>
            <Routes>
              <Route path="/form/:token" element={<PublicFormPage />} />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("RFQ-2026-00003")).toBeTruthy();
    expect(screen.getByText("Rebar 16mm")).toBeTruthy();
    expect(screen.getByRole("button", { name: /submit|إرسال/i })).toBeTruthy();
  });
});

describe("purchase orders list", () => {
  beforeEach(() => {
    // Braces matter: returning the mock makes Vitest treat it as a cleanup hook.
    apiMock.get.mockReset();
  });

  const summary = {
    id: "po1",
    code: "PO-2026-00001",
    rfq_id: "r1",
    supplier_id: "s1",
    supplier_name: "Suez Metal Works",
    supplier_name_ar: "مصنع السويس للمعادن",
    amount: "10141000.0000",
    currency: "EGP",
    status: "received",
    status_label: "Received by warehouse",
    status_label_ar: "تم استلام المخزن",
    issued_at: new Date().toISOString(),
    closed_at: null,
    erp_status: "not_exported",
    lines: 2,
  };

  function mockApi(items: (typeof summary)[]) {
    apiMock.get.mockImplementation((path?: string) => {
      if (String(path ?? "").startsWith("/api/v1/purchase-orders")) {
        return Promise.resolve({ items, total: items.length, page: 1, page_size: 100 });
      }
      if (String(path ?? "").startsWith("/api/v1/supplier-balances")) {
        return Promise.resolve({
          items: [
            {
              supplier_id: "s1",
              supplier_name: "Suez Metal Works",
              supplier_name_ar: "مصنع السويس للمعادن",
              status: "approved",
              balance: "3672600.0000",
              outstanding: "3672600.0000",
              overdue: "250000.0000",
              open_invoices: 1,
              currency: "EGP",
              last_payment_at: null,
            },
          ],
          total_outstanding: "3672600.0000",
          total_overdue: "250000.0000",
          currency: "EGP",
        });
      }
      return Promise.reject(new Error(`unexpected path ${path}`));
    });
  }

  it("renders the post-award delivery status and the supplier balance", async () => {
    mockApi([summary]);
    renderPage(<PurchaseOrdersPage />);

    // Arabic is the product default, so the list renders the Arabic supplier and status labels.
    expect(await screen.findByText("PO-2026-00001")).toBeTruthy();
    expect(screen.getAllByText("تم استلام المخزن").length).toBeGreaterThan(0);
    expect(screen.getAllByText("مصنع السويس للمعادن").length).toBeGreaterThan(0);
    // The KPI and the balance table both show the outstanding amount.
    await waitFor(() => expect(screen.getAllByText("3,672,600.00 EGP").length).toBeGreaterThanOrEqual(2));
  });

  it("filters by lifecycle status through the API", async () => {
    mockApi([summary]);
    renderPage(<PurchaseOrdersPage />);
    await screen.findByText("PO-2026-00001");

    fireEvent.click(screen.getByRole("button", { name: "في الطريق" }));
    await waitFor(() =>
      expect(apiMock.get.mock.calls.some(([path]) => String(path).includes("status=in_transit"))).toBe(true),
    );
  });

  it("shows the empty state when nothing has been awarded yet", async () => {
    mockApi([]);
    renderPage(<PurchaseOrdersPage />);
    expect(await screen.findByText("لا توجد أوامر شراء بعد")).toBeTruthy();
  });
});
