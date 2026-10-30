import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import "../i18n";

const { apiMock, state } = vi.hoisted(() => ({
  apiMock: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), download: vi.fn() },
  state: { permissions: [] as string[] },
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
      user: { id: "u1", email: "storekeeper@nile.example.com", roles: ["warehouse"], permissions: state.permissions, tenant: { name: "Nile", slug: "nile" } },
      status: "authenticated",
      challenge: null,
      login: vi.fn(),
      verifyMfa: vi.fn(),
      logout: vi.fn(),
      refreshUser: vi.fn(),
      can: (permission: string) => state.permissions.includes(permission),
    }),
  };
});

import { ToastProvider } from "@/components/ui";
import PurchaseOrdersPage from "./PurchaseOrdersPage";

const summary = {
  id: "po1",
  code: "PO-2026-00001",
  supplier_name: "Suez Metal Works",
  supplier_name_ar: "مصنع السويس للمعادن",
  amount: "10141000.0000",
  currency: "EGP",
  status: "received",
  issued_at: new Date().toISOString(),
  confirmed_at: null,
  invoice_status: "matched",
  outstanding: "3672600.0000",
};

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

describe("purchase order list for a warehouse user", () => {
  beforeEach(() => {
    // Braces matter: returning the mock makes Vitest treat it as a cleanup hook.
    apiMock.get.mockReset();
    state.permissions = ["po:read", "receipt:create"];
    apiMock.get.mockImplementation((path: string) => {
      if (String(path).startsWith("/api/v1/purchase-orders")) {
        return Promise.resolve({ items: [summary], total: 1, page: 1, page_size: 100 });
      }
      return Promise.reject(new Error(`unexpected path ${path}`));
    });
  });

  it("renders the order without ever calling the financial balance endpoint", async () => {
    renderPage(<PurchaseOrdersPage />);
    expect(await screen.findByText("PO-2026-00001")).toBeTruthy();
    const paths = apiMock.get.mock.calls.map(([path]) => String(path));
    expect(paths.some((path) => path.includes("/supplier-balances"))).toBe(false);
  });
});
