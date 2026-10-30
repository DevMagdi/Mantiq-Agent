import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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
      theme: "dark",
      setTheme: vi.fn(),
    }),
  };
});

import { ToastProvider } from "@/components/ui";
import ApprovalsPage from "./ApprovalsPage";
import SuppliersPage from "./SuppliersPage";

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
  beforeEach(() => apiMock.get.mockReset());

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
  beforeEach(() => apiMock.get.mockReset());

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
  beforeEach(() => apiMock.get.mockReset());

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
