import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import i18n from "@/i18n";

const { apiMock, state } = vi.hoisted(() => ({
  apiMock: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
  state: { lang: "en" as "en" | "ar", permissions: ["admin:integrations"] },
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
      user: { id: "u1", email: "admin@pilot.procureai", full_name: "Admin", roles: ["admin"], permissions: state.permissions },
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
import { IntegrationsPanel } from "./IntegrationsPanel";
import type { IntegrationsPayload } from "@/lib/types";

const payload: IntegrationsPayload = {
  statuses: ["live", "sandbox", "not_configured", "error", "disabled"],
  platform_adapters: { llm: "fake", email: "fake", whatsapp: "fake", telephony: "fake", search: "fake", payment: "fake" },
  catalogue: [
    {
      key: "llm",
      label_en: "Language model",
      label_ar: "نموذج اللغة",
      description_en: "Drafting and extraction.",
      description_ar: "الصياغة والاستخراج.",
      tenant_managed: true,
      providers: [
        {
          key: "anthropic",
          label: "Anthropic Claude",
          fields: [
            { key: "api_key", label_en: "API key", label_ar: "مفتاح الـ API", kind: "password", required: true, secret: true, placeholder: "sk-ant-…", help_en: "", help_ar: "", default: "", options: [] },
            { key: "model", label_en: "Model", label_ar: "النموذج", kind: "text", required: true, secret: false, placeholder: "claude-sonnet-4-5-20250929", help_en: "", help_ar: "", default: "", options: [] },
          ],
        },
        {
          key: "openai_compatible",
          label: "OpenAI / compatible",
          fields: [
            { key: "model", label_en: "Model", label_ar: "النموذج", kind: "text", required: true, secret: false, placeholder: "gpt-4.1", help_en: "", help_ar: "", default: "", options: [] },
            { key: "base_url", label_en: "Base URL", label_ar: "عنوان الخدمة", kind: "url", required: true, secret: false, placeholder: "https://api.openai.com/v1", help_en: "", help_ar: "", default: "", options: [] },
          ],
        },
      ],
    },
    {
      key: "email",
      label_en: "Email",
      label_ar: "البريد الإلكتروني",
      description_en: "RFQ dispatch.",
      description_ar: "إرسال طلبات الأسعار.",
      tenant_managed: true,
      providers: [
        {
          key: "smtp",
          label: "SMTP",
          fields: [
            { key: "host", label_en: "SMTP host", label_ar: "خادم SMTP", kind: "text", required: true, secret: false, placeholder: "smtp.office365.com", help_en: "", help_ar: "", default: "", options: [] },
            { key: "password", label_en: "Password", label_ar: "كلمة المرور", kind: "password", required: false, secret: true, placeholder: "", help_en: "", help_ar: "", default: "", options: [] },
          ],
        },
      ],
    },
    {
      key: "payment",
      label_en: "Payments",
      label_ar: "المدفوعات",
      description_en: "Recording only.",
      description_ar: "تسجيل فقط.",
      tenant_managed: false,
      providers: [],
    },
  ],
  items: [
    {
      kind: "llm",
      provider: "anthropic",
      provider_source: "tenant",
      platform_provider: "fake",
      status: "live",
      enabled: true,
      tenant_managed: true,
      config: { provider: "anthropic", model: "claude-sonnet-4-5-20250929" },
      secrets_saved: ["api_key"],
      masked: { api_key: "••••••••te-st" },
      missing: [],
      last_tested_at: "2026-10-31T09:10:00Z",
      last_error: "",
      last_test_ok: true,
    },
    {
      kind: "email",
      provider: "smtp",
      provider_source: "platform",
      platform_provider: "fake",
      status: "error",
      enabled: true,
      tenant_managed: true,
      config: { host: "smtp.office365.com" },
      secrets_saved: [],
      masked: {},
      missing: ["from_address"],
      last_tested_at: null,
      last_error: "SMTP connect failed",
      last_test_ok: false,
    },
    {
      kind: "payment",
      provider: "fake",
      provider_source: "platform",
      platform_provider: "fake",
      status: "sandbox",
      enabled: true,
      tenant_managed: false,
      config: {},
      secrets_saved: [],
      masked: {},
      missing: [],
      last_tested_at: null,
      last_error: "",
      last_test_ok: null,
    },
  ],
};

function renderPanel(): ReactElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <IntegrationsPanel lang={state.lang} />
      </ToastProvider>
    </QueryClientProvider>
  );
}

beforeEach(async () => {
  state.lang = "en";
  state.permissions = ["admin:integrations"];
  apiMock.get.mockReset();
  apiMock.put.mockReset();
  apiMock.post.mockReset();
  apiMock.patch.mockReset();
  apiMock.get.mockResolvedValue(payload);
  await i18n.changeLanguage("en");
});

describe("the integrations screen", () => {
  it("renders the catalogue fields instead of an empty JSON box", async () => {
    render(renderPanel());

    expect(await screen.findByLabelText(/API key/i)).toBeTruthy();
    expect(screen.getByLabelText(/^Model/i)).toBeTruthy();
    expect(screen.getByLabelText(/SMTP host/i)).toBeTruthy();
    expect(screen.queryByText(/Config \(JSON\)/i)).not.toBeTruthy();
  });

  it("shows the four honest statuses and the source of each configuration", async () => {
    render(renderPanel());

    expect(await screen.findByText("Live")).toBeTruthy();
    expect(screen.getByText("Error")).toBeTruthy();
    expect(screen.getByText("Sandbox")).toBeTruthy();
    expect(screen.getByText("configured for this tenant")).toBeTruthy();
    expect(screen.getByText("platform default")).toBeTruthy();
  });

  it("masks a stored secret and never renders the raw value", async () => {
    render(renderPanel());

    const key = await screen.findByLabelText(/API key/i);
    expect(key.getAttribute("type")).toBe("password");
    expect(key.getAttribute("placeholder")).toBe("••••••••te-st");
    expect(document.body.textContent ?? "").not.toContain("sk-ant-api03");
  });

  it("saves real credentials through the API and reports the new state", async () => {
    apiMock.put.mockResolvedValue({
      ...payload.items[0], status: "live", secrets_changed: ["api_key"], missing: [], masked: { api_key: "••••test" },
    });
    render(renderPanel());

    fireEvent.change(await screen.findByLabelText(/API key/i), { target: { value: "sk-ant-real-key" } });
    const card = screen.getByLabelText(/API key/i).closest("div")?.parentElement?.parentElement;
    const buttons = card ? Array.from(card.querySelectorAll("button")) : [];
    const saveButton = buttons.find((b) => /save/i.test(b.textContent ?? ""));
    expect(saveButton).toBeTruthy();
    fireEvent.click(saveButton as HTMLButtonElement);

    await waitFor(() => expect(apiMock.put).toHaveBeenCalled());
    const call = apiMock.put.mock.calls[0] as [string, { provider: string; config: Record<string, string>; secrets: Record<string, string>; enabled: boolean }];
    const [path, body] = call;
    expect(path).toBe("/api/v1/integrations/llm");
    expect(body.secrets).toEqual({ api_key: "sk-ant-real-key" });
    expect(body.provider).toBe("anthropic");
    expect(body.config.model).toBe("claude-sonnet-4-5-20250929");
    expect(body.enabled).toBe(true);
  });

  it("tests a connection and shows the vendor answer with its latency", async () => {
    apiMock.post.mockResolvedValue({ ok: true, status: "live", detail: "anthropic:claude · reply “pong”", latency_ms: 42, probe: "anthropic" });
    render(renderPanel());

    const buttons = await screen.findAllByRole("button", { name: /test connection/i });
    fireEvent.click(buttons[0] as HTMLButtonElement);

    await waitFor(() => expect(apiMock.post).toHaveBeenCalledWith("/api/v1/integrations/llm/test"));
    expect(await screen.findByText(/reply “pong”/)).toBeTruthy();
    expect(screen.getAllByText(/42 ms/).length).toBeGreaterThan(0);
  });

  it("disables an integration and hands traffic back to the platform", async () => {
    apiMock.patch.mockResolvedValue({ enabled: false, status: "disabled" });
    render(renderPanel());

    const disable = await screen.findAllByRole("button", { name: /disable/i });
    fireEvent.click(disable[0] as HTMLButtonElement);

    await waitFor(() => expect(apiMock.patch).toHaveBeenCalledWith("/api/v1/integrations/llm", { enabled: false }));
  });

  it("lists platform-managed adapters read-only, without pretending they are editable", async () => {
    render(renderPanel());

    expect(await screen.findByText(/Managed on the platform/i)).toBeTruthy();
    expect(screen.getByText("Payments")).toBeTruthy();
    expect(screen.getAllByLabelText(/API key/i)).toHaveLength(1);
  });

  it("speaks Arabic with the same fields when the interface is Arabic", async () => {
    state.lang = "ar";
    await i18n.changeLanguage("ar");
    render(renderPanel());

    expect(await screen.findByLabelText(/مفتاح الـ API/)).toBeTruthy();
    expect(screen.getByText("يعمل فعليًا")).toBeTruthy();
    expect(screen.getByText("خطأ")).toBeTruthy();
  });
});
