import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import "../i18n";

const { state } = vi.hoisted(() => ({ state: { permissions: [] as string[] } }));

vi.mock("@/lib/session", async () => {
  const actual = await vi.importActual<typeof import("@/lib/session")>("@/lib/session");
  return {
    ...actual,
    useAuth: () => ({
      user: { id: "u1", email: "requester@nile.example.com", full_name: "Requester", roles: ["requester"], permissions: state.permissions, tenant: { name: "Nile", slug: "nile" } },
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

import { RequirePermission, RestrictedPanel } from "./Guard";
import { ToastProvider } from "./ui";

function renderWithProviders(element: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>{element}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("permission guard", () => {
  beforeEach(() => {
    state.permissions = [];
  });

  it("renders the screen when the role holds the permission", () => {
    state.permissions = ["audit:read"];
    renderWithProviders(
      <RequirePermission permission="audit:read">
        <p>agent activity</p>
      </RequirePermission>,
    );
    expect(screen.getByText("agent activity")).toBeTruthy();
  });

  it("shows a calm explanation, not an error, when the permission is missing", () => {
    renderWithProviders(
      <RequirePermission permission="audit:read">
        <p>agent activity</p>
      </RequirePermission>,
    );
    expect(screen.queryByText("agent activity")).toBeNull();
    expect(screen.getByText("ليست لديك صلاحية الوصول إلى هذه الشاشة")).toBeTruthy();
    expect(screen.getByText("audit:read")).toBeTruthy();
  });

  it("renders a restricted panel for a dashboard card the role cannot read", () => {
    renderWithProviders(<RestrictedPanel title="Approvals" permission="approval:read" />);
    expect(screen.getByText("Approvals")).toBeTruthy();
    expect(screen.getByText("approval:read")).toBeTruthy();
  });
});
