import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "./i18n";

/**
 * Whole-app RBAC proof: sign in as the low-privilege `requester` role, visit every screen it is
 * allowed to reach, and assert that the app never fires a request that the API would answer with
 * 403. These are the exact endpoints that used to spam the console:
 * `/api/v1/approvals`, `/api/v1/agent-runs`, `/api/v1/agent/tools` (plus the admin surfaces).
 *
 * The permission list is the real one from `backend/app/core/rbac.py` for `requester`, and a
 * backend test (`tests/test_frontend_permissions.py`) keeps the two maps in sync.
 */

const REQUESTER_PERMISSIONS = [
  "pr:read", "pr:create", "pr:update", "rfq:read", "supplier:read", "offer:read",
  "comparison:read", "comment:create", "report:read", "settings:read",
  "po:read", "receipt:confirm",
];

const FORBIDDEN_FOR_REQUESTER = [
  "/api/v1/approvals",
  "/api/v1/agent-runs",
  "/api/v1/agent/tools",
  "/api/v1/audit",
  "/api/v1/retention", // allowed by the API but only rendered for admin tabs we never open here
  "/api/v1/api-keys",
  "/api/v1/llm-config",
  "/api/v1/billing",
  "/api/v1/integrations",
  "/api/v1/supplier-balances",
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
        email: "requester@nile.example.com",
        full_name: "Mariam Adel",
        roles: ["requester"],
        permissions: REQUESTER_PERMISSIONS,
        tenant: { name: "Nile", slug: "nile", plan: "growth" },
      },
      status: "authenticated",
      challenge: null,
      login: vi.fn(),
      verifyMfa: vi.fn(),
      logout: vi.fn(),
      refreshUser: vi.fn(),
      can: (permission: string) => REQUESTER_PERMISSIONS.includes(permission),
    }),
  };
});

// The Shell mounts the realtime hook; a socket in jsdom would try to dial a server.
vi.mock("@/lib/realtime", () => ({ useRealtime: () => true }));

import App from "./App";
import { ToastProvider } from "./components/ui";

/** Every panel the app can load, in the shape each screen expects. */
const RESPONSES: Record<string, unknown> = {
  "/api/v1/notifications": { items: [], unread: 0 },
  "/api/v1/dashboard": {
    kpis: {
      period_days: 30,
      rfqs_total: 0,
      rfqs_open: 0,
      offers_total: 0,
      purchase_orders: 0,
      pending_approvals: 0,
      cycle_time_days: 0,
      savings: { initial: "0", final: "0", savings: 0, savings_pct: 0, savings_amount: "0" },
    },
    recent_rfqs: [],
    activity: [],
    approvals: [],
    spend_by_category: [],
    savings_by_month: [],
  },
  "/api/v1/requests": { items: [], total: 0, page: 1, page_size: 100 },
  "/api/v1/rfqs/board": { stages: [] },
  "/api/v1/rfqs": { items: [], total: 0, page: 1, page_size: 100 },
  "/api/v1/suppliers": { items: [], total: 0, page: 1 },
  "/api/v1/purchase-orders": { items: [], total: 0, page: 1, page_size: 100 },
  "/api/v1/plants": { items: [], total: 0, page: 1 },
  "/api/v1/meta/capabilities": { adapters: {}, features: {} },
  "/api/v1/permissions": [],
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
  return apiMock.get.mock.calls.map(([path]) => String(path));
}

function forbiddenCalls(): string[] {
  return calledPaths().filter((path) => FORBIDDEN_FOR_REQUESTER.some((prefix) => path.startsWith(prefix)));
}

describe("a requester never triggers a forbidden request", () => {
  beforeEach(() => {
    apiMock.get.mockReset();
    apiMock.post.mockReset();
    apiMock.get.mockImplementation((path: string) => {
      const key = Object.keys(RESPONSES).find((prefix) => String(path).startsWith(prefix));
      return key ? Promise.resolve(RESPONSES[key]) : Promise.resolve({ items: [], total: 0 });
    });
    apiMock.post.mockResolvedValue({ ok: true });
  });

  afterEach(() => {
    vi.clearAllTimers();
  });

  for (const path of ["/", "/requests", "/rfqs", "/purchase-orders", "/suppliers", "/settings"]) {
    it(`makes no forbidden call on ${path}`, async () => {
      renderAt(path);
      await waitFor(() => expect(apiMock.get).toHaveBeenCalled());
      // Let every child query settle (dashboard cards, settings tabs…).
      await new Promise((resolve) => setTimeout(resolve, 60));
      expect(forbiddenCalls()).toEqual([]);
    });
  }

  it("renders an explained panel instead of the approvals screen", async () => {
    renderAt("/approvals");
    expect(await screen.findByText("ليست لديك صلاحية الوصول إلى هذه الشاشة")).toBeTruthy();
    expect(forbiddenCalls()).toEqual([]);
  });

  it("renders an explained panel instead of the agent activity screen", async () => {
    renderAt("/activity");
    expect(await screen.findByText("ليست لديك صلاحية الوصول إلى هذه الشاشة")).toBeTruthy();
    expect(forbiddenCalls()).toEqual([]);
  });

  it("hides the navigation entries the role cannot use", async () => {
    renderAt("/");
    await screen.findByRole("navigation");
    const nav = screen.getByRole("navigation", { name: "التنقل الرئيسي" });
    const labels = Array.from(nav.querySelectorAll("a")).map((link) => link.textContent ?? "");
    expect(labels.some((label) => label.includes("الاعتمادات"))).toBe(false);
    expect(labels.some((label) => label.includes("نشاط"))).toBe(false);
    // It still shows everything the role can actually use.
    expect(labels.some((label) => label.includes("طلبات الشراء"))).toBe(true);
  });

  it("shows a restricted card on the dashboard rather than a 403", async () => {
    renderAt("/");
    expect(await screen.findByText("الاعتمادات")).toBeTruthy();
    expect(screen.getAllByText("أُخفيت حسب دورك حتى لا تُرسل الصفحة طلبًا سيُرفض.").length).toBeGreaterThan(0);
  });
});
