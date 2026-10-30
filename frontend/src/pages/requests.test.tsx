import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import "../i18n";

const { apiMock } = vi.hoisted(() => ({
  apiMock: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), download: vi.fn() },
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
      user: { id: "u1", email: "requester@nile.example.com", full_name: "Requester", roles: ["requester"], permissions: ["pr:update"], tenant: { name: "Nile", slug: "nile" } },
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
import { RequestDetail } from "./RequestsPage";
import type { PurchaseRequest } from "@/lib/types";

const baseRequest: PurchaseRequest = {
  id: "pr1",
  code: "PR-2026-00009",
  title: "Rebar 16mm",
  status: "clarifying",
  source: "text",
  currency: "EGP",
  total_estimate: "410000.0000",
  confidence: 0.72,
  needed_by: null,
  plant_id: null,
  clarification_resolved: false,
  created_at: new Date().toISOString(),
  lines: [],
  clarifications: [
    { id: "q1", field: "delivery_location", question: "Where should the rebar be delivered?", status: "open" },
    { id: "q2", field: "standard", question: "Which grade?", answer: "B420D", status: "answered" },
  ],
};

function renderDetail(request: PurchaseRequest, onChanged = vi.fn(async () => undefined)) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: 0 } } });
  const element: ReactElement = (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>
          <RequestDetail request={request} onChanged={onChanged} />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
  return render(element);
}

describe("clarification flow", () => {
  beforeEach(() => {
    apiMock.post.mockReset();
    apiMock.get.mockReset();
  });

  it("blocks submit while a clarification is still open and says why", () => {
    renderDetail(baseRequest);
    const submit = screen.getByRole("button", { name: "إرسال لإنشاء طلب عرض" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/احسم أو تجاوز/)).toBeTruthy();
  });

  it("enables submit once every question is answered or waived", () => {
    const resolved: PurchaseRequest = {
      ...baseRequest,
      clarifications: [
        { id: "q1", field: "delivery_location", question: "Where?", answer: "Alexandria", status: "answered" },
        { id: "q2", field: "standard", question: "Which grade?", status: "waived" },
      ],
    };
    renderDetail(resolved);
    const submit = screen.getByRole("button", { name: "إرسال لإنشاء طلب عرض" });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
  });

  it("waives a question through the API instead of submitting an empty answer", async () => {
    apiMock.post.mockResolvedValue({ ok: true });
    renderDetail(baseRequest);
    fireEvent.click(screen.getByRole("button", { name: "تجاوز" }));
    await waitFor(() => expect(apiMock.post).toHaveBeenCalled());
    const [path, body] = apiMock.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/api/v1/requests/pr1/clarifications/q1/answer");
    expect(body).toMatchObject({ waive: true });
  });

  it("surfaces the 422 message inline instead of rejecting unhandled", async () => {
    const resolved: PurchaseRequest = { ...baseRequest, clarifications: [] };
    apiMock.post.mockRejectedValue(new Error("Resolve or waive all clarification questions before creating an RFQ."));
    renderDetail(resolved);
    fireEvent.click(screen.getByRole("button", { name: "إرسال لإنشاء طلب عرض" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Resolve or waive all clarification questions");
  });
});
