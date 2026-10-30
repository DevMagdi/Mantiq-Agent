import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import i18n from "../i18n";

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
      user: {
        id: "u1",
        email: "controller@nile.example.com",
        full_name: "Nour",
        roles: ["controller"],
        permissions: state.permissions,
        tenant: { name: "Nile", slug: "nile" },
      },
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
import JournalPage from "./JournalPage";
import AccountingReportsPage from "./AccountingReportsPage";
import { P2PRail } from "@/components/P2PRail";
import type { JournalEntryView, Lifecycle } from "@/lib/types";

const entry: JournalEntryView = {
  id: "je1",
  number: "JE-2026-00001",
  entry_date: new Date().toISOString(),
  currency: "EGP",
  memo: "Goods received for PO-2026-00001",
  memo_ar: "استلام بضاعة لأمر الشراء",
  status: "draft",
  status_label: "Draft",
  status_label_ar: "مسودة",
  source_type: "receipt_accrual",
  source_label: "Goods received",
  source_label_ar: "استلام بضاعة",
  source_id: "po1",
  po_id: "po1",
  invoice_id: null,
  receipt_id: "gr1",
  supplier_id: "s1",
  total_debit: "4500.0000",
  total_credit: "4500.0000",
  balanced: true,
  created_by: "u1",
  created_at: new Date().toISOString(),
  posted_by: null,
  posted_at: null,
  reversal_of: null,
  reversed_by: null,
  reversal_reason: "",
  lines: [
    {
      line_no: 1,
      account_code: "110100",
      account_name: "Inventory",
      description: "Received: Steel plate",
      debit: "4500.0000",
      credit: "0",
      cost_center_code: "CC-000",
      unit_number: null,
    },
    {
      line_no: 2,
      account_code: "110200",
      account_name: "GR/IR clearing",
      description: "GR/IR clearing",
      debit: "0",
      credit: "4500.0000",
    },
  ],
};

const lifecycle: Lifecycle = {
  po_id: "po1",
  current: "pending_receipt",
  current_label_en: "Pending receipt",
  current_label_ar: "بانتظار الاستلام",
  next_action_en: "Record the goods receipt at the warehouse",
  next_action_ar: "سجّل الاستلام بالمخزن",
  stages: [
    ["requested", "Requested", "تم الطلب", "done"],
    ["in_procurement", "In procurement", "قيد المعالجة", "done"],
    ["rfq_sent", "RFQ sent", "أُرسل الطلب", "done"],
    ["offers_received", "Offers received", "تم استلام العروض", "done"],
    ["under_approval", "Under approval", "قيد الاعتماد", "done"],
    ["po_issued", "PO issued", "صدر الأمر", "done"],
    ["pending_receipt", "Pending receipt", "بانتظار الاستلام", "current"],
    ["received_by_warehouse", "Received", "استُلم", "pending"],
    ["confirmed_by_requester", "Confirmed", "أُكِّد", "pending"],
    ["ready_for_audit", "Ready for audit", "جاهز للمراجعة", "pending"],
    ["posted_to_accounts", "Posted", "مُرحَّل", "pending"],
  ].map(([code, en, ar, stageState], index) => ({
    code: code as string,
    label_en: en as string,
    label_ar: ar as string,
    order: index,
    state: stageState as "done" | "current" | "pending",
    reached_at: stageState === "pending" ? null : new Date().toISOString(),
    actor_id: stageState === "pending" ? null : "u1",
  })),
  history: [
    { stage: "requested", reached_at: new Date().toISOString(), actor_id: "u1", source: "pr.created" },
  ],
};

// The product defaults to Arabic; these assertions read the English labels.
beforeAll(async () => {
  await i18n.changeLanguage("en");
});

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

describe("journal entries", () => {
  beforeEach(() => {
    apiMock.get.mockReset();
    apiMock.post.mockReset();
    state.permissions = ["accounting:read", "accounting:write"];
    apiMock.get.mockImplementation((path: string) => {
      const value = String(path);
      if (value.startsWith("/api/v1/accounting/journal-entries")) {
        return Promise.resolve({ items: [entry], total: 1, posted: 0, posted_total: "0" });
      }
      if (value.startsWith("/api/v1/accounting/accounts")) {
        return Promise.resolve({ items: [{ code: "110100", name: "Inventory", name_ar: "المخزون", kind: "asset", is_active: true }] });
      }
      if (value.startsWith("/api/v1/accounting/cost-centers")) {
        return Promise.resolve({ items: [{ code: "CC-000", name: "Unallocated", name_ar: "غير موزع" }] });
      }
      return Promise.reject(new Error(`unexpected path ${path}`));
    });
  });

  it("lists the draft and never offers posting without the posting permission", async () => {
    renderPage(<JournalPage />);
    expect(await screen.findByText("JE-2026-00001")).toBeTruthy();
    fireEvent.click(screen.getByText("JE-2026-00001"));
    expect((await screen.findAllByText(/Goods received for PO-2026-00001/)).length).toBeGreaterThan(0);
    // A controller who cannot post sees the evidence but not the post button.
    expect(screen.queryByRole("button", { name: /Post|ترحيل/ })).toBeNull();
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("posts the entry when the permission is present", async () => {
    state.permissions = ["accounting:read", "accounting:post", "accounting:write"];
    apiMock.post.mockResolvedValue({ ...entry, status: "posted" });
    renderPage(<JournalPage />);
    fireEvent.click(await screen.findByText("JE-2026-00001"));
    const postButton = await screen.findByRole("button", { name: /Post|ترحيل/ });
    fireEvent.click(postButton);
    await waitFor(() => expect(apiMock.post).toHaveBeenCalledWith("/api/v1/accounting/journal-entries/je1/post"));
  });
});

describe("cost and ledger reports", () => {
  beforeEach(() => {
    apiMock.get.mockReset();
    state.permissions = ["accounting:read", "cost:read"];
    apiMock.get.mockImplementation((path: string) => {
      const value = String(path);
      if (value.startsWith("/api/v1/accounting/trial-balance")) {
        return Promise.resolve({
          items: [
            { code: "110100", account_code: "110100", account_name: "Inventory", account_name_ar: "المخزون", kind: "asset", debit: "4500", credit: "0", balance: "4500" },
          ],
          total_debit: "4500",
          total_credit: "4500",
          balanced: true,
          currency: "EGP",
        });
      }
      if (value.startsWith("/api/v1/accounting/cost-report")) {
        return Promise.resolve({
          group_by: "cost_center",
          items: [{ key: "CC-000", label: "Unallocated", label_ar: "غير موزع", amount: "4500", share_pct: 100 }],
          total: "4500",
          currency: "EGP",
        });
      }
      return Promise.reject(new Error(`unexpected path ${path}`));
    });
  });

  it("shows the trial balance and the cost split", async () => {
    renderPage(<AccountingReportsPage />);
    expect(await screen.findByText("Inventory")).toBeTruthy();
    // Radix activates a tab on pointer-down, not on click.
    fireEvent.mouseDown(screen.getByRole("tab", { name: /Cost analysis|تحليل التكلفة/ }));
    expect(await screen.findByText("Unallocated")).toBeTruthy();
    // The supplier sub-ledger is financial data: a user without `ledger:read` never requests it.
    const paths = apiMock.get.mock.calls.map(([path]) => String(path));
    expect(paths.some((path) => path.includes("/supplier-balances"))).toBe(false);
  });
});

describe("purchase-to-pay rail", () => {
  it("marks done, current and pending stages and shows the next action", () => {
    renderPage(<P2PRail lifecycle={lifecycle} />);
    expect(screen.getAllByRole("button").length).toBe(11);
    expect(screen.getByText(/Record the goods receipt at the warehouse|سجّل الاستلام بالمخزن/)).toBeTruthy();
  });
});

describe("supplier sub-ledger", () => {
  const balance = {
    supplier_id: "s1",
    supplier_name: "Nile Steel",
    supplier_name_ar: "حديد النيل",
    outstanding: "2448400",
    overdue: "0",
    invoiced_total: "2448400",
    paid_total: "0",
    currency: "EGP",
    open_invoices: 1,
  };

  beforeEach(() => {
    apiMock.get.mockReset();
    apiMock.post.mockReset();
    state.permissions = ["accounting:read", "cost:read", "ledger:read"];
    apiMock.get.mockImplementation((path: string) => {
      const value = String(path);
      if (value.startsWith("/api/v1/accounting/trial-balance")) {
        return Promise.resolve({ items: [], total_debit: "0", total_credit: "0", balanced: true, currency: "EGP" });
      }
      if (value.startsWith("/api/v1/accounting/cost-report")) {
        return Promise.resolve({ group_by: "cost_center", items: [], total: "0", currency: "EGP" });
      }
      if (value.startsWith("/api/v1/supplier-balances")) {
        return Promise.resolve({ items: [balance], total_outstanding: "2448400", total_overdue: "0", currency: "EGP" });
      }
      if (value.startsWith("/api/v1/accounting/supplier-account/s1")) {
        return Promise.resolve({
          supplier_id: "s1",
          supplier_name: "Nile Steel",
          supplier_name_ar: "حديد النيل",
          payable: "2448400",
          debit_total: "0",
          credit_total: "2448400",
          movements: [
            {
              entry_id: "je2",
              number: "JE-2026-00002",
              entry_date: new Date().toISOString(),
              source_type: "invoice",
              source_label: "Supplier invoice — GR/IR clearing and payable",
              source_label_ar: "فاتورة مورد",
              description: "Invoice INV-00001 — Nile Steel",
              debit: "0",
              credit: "2448400",
              invoice_id: "inv1",
            },
          ],
          ledger_entries: [
            {
              id: "le1",
              kind: "invoice",
              amount: "2448400",
              currency: "EGP",
              entry_date: new Date().toISOString(),
              method: "",
              reference: "INV-00001",
              invoice_id: "inv1",
              po_id: "po1",
            },
          ],
          balance,
        });
      }
      return Promise.reject(new Error(`unexpected path ${path}`));
    });
  });

  it("opens the supplier ledger from the balances tab with the payable movements", async () => {
    renderPage(<AccountingReportsPage />);
    fireEvent.mouseDown(await screen.findByRole("tab", { name: /Supplier balances|أرصدة الموردين/ }));
    expect(await screen.findByText("Nile Steel")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Open ledger|فتح الحساب/ }));
    expect(await screen.findByText("JE-2026-00002")).toBeTruthy();
    expect(screen.getByText(/Supplier invoice — GR\/IR clearing and payable/)).toBeTruthy();
    expect(apiMock.get).toHaveBeenCalledWith("/api/v1/accounting/supplier-account/s1");
    // Read-only: opening a ledger never writes.
    expect(apiMock.post).not.toHaveBeenCalled();
  });

  it("hides the balances tab entirely without the ledger permission", async () => {
    state.permissions = ["accounting:read", "cost:read"];
    renderPage(<AccountingReportsPage />);
    await screen.findByText(/Total debits|إجمالي المدين/);
    expect(screen.queryByRole("tab", { name: /Supplier balances|أرصدة الموردين/ })).toBeNull();
    const paths = apiMock.get.mock.calls.map(([path]) => String(path));
    expect(paths.some((path) => path.includes("supplier-account"))).toBe(false);
  });
});
