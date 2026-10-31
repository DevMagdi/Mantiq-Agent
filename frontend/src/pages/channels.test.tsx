import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import i18n from "../i18n";

const { apiMock, state } = vi.hoisted(() => ({
  apiMock: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn(), upload: vi.fn() },
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
      user: { id: "u1", email: "buyer@nile.example.com", full_name: "Bassem", roles: ["buyer"], permissions: state.permissions },
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
import { RfqChannelPanel } from "@/components/RfqChannelPanel";
import InboxPage from "./InboxPage";
import VoiceCallsPage from "./VoiceCallsPage";
import AssistantPage from "./AssistantPage";
import TemplatesPage from "./TemplatesPage";
import type { AssistantAnswer, InboxList, InboxThreadDetail, RfqChannels, TemplateLibrary, VoiceCallDetail } from "@/lib/types";

const now = "2026-10-31T09:00:00Z";

const inboxList: InboxList = {
  items: [
    {
      thread_id: "t1",
      rfq_id: "r1",
      rfq_code: "RFQ-2026-00001",
      rfq_title: "Rebar 12mm",
      supplier_id: "s1",
      supplier_name: "Nile Steel",
      supplier_name_ar: "حديد النيل",
      current_channel: "whatsapp",
      channels_used: ["email", "whatsapp"],
      last_message: {
        id: "m2",
        direction: "inbound",
        channel: "whatsapp",
        channel_label: "WhatsApp",
        channel_label_ar: "واتساب",
        author_type: "supplier",
        author_id: null,
        status: "received",
        delivery_status: "",
        delivered_at: null,
        read_at: null,
        subject: "",
        body: "السعر 21000 للطن",
        body_ar: "السعر 21000 للطن",
        media_type: "text",
        template_code: "",
        locale: "ar",
        created_at: now,
      },
      last_message_at: now,
      message_count: 2,
      inbound_count: 1,
      outbound_count: 1,
      comm_status: "replied",
      comm_status_label: "Replied",
      comm_status_label_ar: "ردّ المورد",
      awaiting_reply: false,
      preferred_channel: "whatsapp",
    },
  ],
  total: 1,
  channels: ["email", "whatsapp"],
  awaiting_reply: 0,
  generated_at: now,
};

const threadDetail: InboxThreadDetail = {
  thread_id: "t1",
  rfq_id: "r1",
  rfq_code: "RFQ-2026-00001",
  rfq_title: "Rebar 12mm",
  supplier_id: "s1",
  supplier_name: "Nile Steel",
  supplier_name_ar: "حديد النيل",
  current_channel: "whatsapp",
  reply_channel: "whatsapp",
  channels_used: ["email", "whatsapp"],
  messages: [
    {
      ...inboxList.items[0]!.last_message,
      id: "m1",
      direction: "outbound",
      channel: "email",
      channel_label: "Email",
      channel_label_ar: "البريد الإلكتروني",
      body: "Please quote",
      delivery_status: "sent",
    },
    { ...inboxList.items[0]!.last_message },
  ],
  events: [],
  resolved_channel: {
    channel: "whatsapp",
    channel_label: "WhatsApp",
    channel_label_ar: "واتساب",
    available: true,
    reasons: [],
  },
  comm_status: "replied",
};

const rfqChannels: RfqChannels = {
  rfq_id: "r1",
  rfq_code: "RFQ-2026-00001",
  items: [
    {
      supplier_id: "s1",
      supplier_name: "Nile Steel",
      supplier_name_ar: "حديد النيل",
      channel: "whatsapp",
      channel_label: "WhatsApp",
      channel_label_ar: "واتساب",
      source: "supplier_preference",
      source_label: "Supplier preference",
      source_label_ar: "تفضيل المورد",
      available: true,
      opted_in: true,
      reasons: [],
      comm_status: "replied",
      comm_status_label: "Replied",
      comm_status_label_ar: "ردّ المورد",
      invitations: 1,
      replies: 1,
      last_message_at: now,
      delivered_at: now,
      read_at: now,
      channel_override: null,
    },
  ],
  channels_in_use: ["whatsapp"],
  mixed_channels: false,
};

const templateLibrary: TemplateLibrary = {
  channels: [
    { channel: "whatsapp", label: "WhatsApp", label_ar: "واتساب", count: 1 },
    { channel: "email", label: "Email", label_ar: "البريد الإلكتروني", count: 1 },
  ],
  items: {
    whatsapp: [
      {
        id: "tpl1",
        code: "reminder",
        kind: "reminder",
        channel: "whatsapp",
        locale: "en",
        name: "Reminder",
        subject: "",
        body: "Reminder for {{rfq_code}}: deadline {{deadline}}",
        whatsapp_template_name: "reminder_en",
        version: 1,
        description: "",
        variables: ["rfq_code", "deadline"],
      },
    ],
    email: [
      {
        id: "tpl2",
        code: "rfq_invitation",
        kind: "invitation",
        channel: "email",
        locale: "en",
        name: "Invitation",
        subject: "RFQ {{rfq_code}}",
        body: "Please quote {{rfq_code}}",
        whatsapp_template_name: "",
        version: 1,
        description: "",
        variables: ["rfq_code"],
      },
    ],
  },
  total: 2,
};

const voiceCalls = {
  items: [
    {
      id: "c1",
      rfq_id: "r1",
      supplier_id: "s1",
      status: "completed",
      disclosure_played: true,
      consent_given: true,
      recording: true,
      duration_seconds: 96,
      extracted_offer_id: "o1",
      blocked_turns: 1,
      created_at: now,
    },
  ],
};

const voiceDetail: VoiceCallDetail = {
  call_id: "c1",
  rfq_id: "r1",
  rfq_code: "RFQ-2026-00001",
  supplier_id: "s1",
  supplier_name: "Nile Steel",
  status: "completed",
  language: "ar-EG",
  disclosure_played: true,
  consent_given: true,
  recording: true,
  duration_seconds: 96,
  transcript: "[AI] disclosure",
  summary: "",
  turns: [
    { seq: 1, speaker: "ai", text: "مرحباً، أنا مساعد ذكاء اصطناعي", topic: "disclosure", decision: "disclose", block_code: "", reason: "" },
    { seq: 2, speaker: "supplier", text: "أسعار المنافسين كام؟", topic: "competitor", decision: "block", block_code: "COMPETITOR_DISCLOSURE", reason: "Blocked" },
  ],
  policy_snapshot: {
    ai_agent: {
      knowledge_version: "approved-data-v1",
      topics_answered: ["price"],
      topics_blocked: ["COMPETITOR_DISCLOSURE"],
      blocked_turns: 1,
      guard: "no competitor data, no commitment, no specification change, no payment",
    },
  },
  policy: { allowed_topics: ["price"], blocked_topics: ["competitor"] },
  extraction_id: "e1",
  created_at: now,
};

const assistantAnswer: AssistantAnswer = {
  ok: true,
  session_id: "as1",
  intent: "forbidden",
  channel: "text",
  locale: "en",
  answer_en: "I cannot do that.",
  answer_ar: "لا أستطيع فعل ذلك.",
  data: {},
  citations: [{ type: "rfq", id: "r1", code: "RFQ-2026-00001", label: "RFQ", link: "/rfqs/r1" }],
  blocked_code: "ASSISTANT_FORBIDDEN_ACTION",
  can_act: false,
  asked_at: now,
};

function wrap(element: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>{element}</ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

beforeAll(async () => {
  await i18n.changeLanguage("en");
});

beforeEach(() => {
  Object.values(apiMock).forEach((fn) => fn.mockReset());
  state.permissions = ["channel:read", "channel:write", "rfq:send", "inbox:read", "inbox:reply", "voice:call", "assistant:use", "rfq:read"];
});

describe("unified inbox", () => {
  it("shows every channel of a conversation and the message history", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path.startsWith("/api/v1/inbox?")) return Promise.resolve(inboxList);
      if (path.startsWith("/api/v1/inbox/threads/")) return Promise.resolve(threadDetail);
      if (path.startsWith("/api/v1/templates/library")) return Promise.resolve(templateLibrary);
      return Promise.reject(new Error(path));
    });

    render(wrap(<InboxPage />));

    expect(await screen.findByText("Nile Steel")).toBeTruthy();
    expect(screen.getAllByText("WhatsApp").length).toBeGreaterThan(0);
    expect(await screen.findByText("Please quote")).toBeTruthy();
    expect(screen.getAllByText("السعر 21000 للطن").length).toBeGreaterThan(0);
  });

  it("sends the reply on the channel the supplier used", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path.startsWith("/api/v1/inbox?")) return Promise.resolve(inboxList);
      if (path.startsWith("/api/v1/inbox/threads/")) return Promise.resolve(threadDetail);
      if (path.startsWith("/api/v1/templates/library")) return Promise.resolve(templateLibrary);
      return Promise.reject(new Error(path));
    });
    apiMock.post.mockResolvedValue({ ok: true, channel: "whatsapp" });

    render(wrap(<InboxPage />));
    const box = await screen.findByLabelText("Write your reply…");
    fireEvent.change(box, { target: { value: "Any update?" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => expect(apiMock.post).toHaveBeenCalledTimes(1));
    expect(apiMock.post).toHaveBeenCalledWith("/api/v1/inbox/threads/t1/reply", {
      body: "Any update?",
      channel: undefined,
      locale: "en",
    });
  });

  it("hides the composer when the role may only read", async () => {
    state.permissions = ["inbox:read"];
    apiMock.get.mockImplementation((path: string) => {
      if (path.startsWith("/api/v1/inbox?")) return Promise.resolve(inboxList);
      if (path.startsWith("/api/v1/inbox/threads/")) return Promise.resolve(threadDetail);
      return Promise.reject(new Error(path));
    });

    render(wrap(<InboxPage />));
    expect(await screen.findByText(/can read this inbox/i)).toBeTruthy();
    expect(screen.queryByLabelText("Write your reply…")).toBeNull();
  });
});

describe("rfq channel panel", () => {
  it("shows the resolved channel, the reason and the communication status", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path.endsWith("/channels")) return Promise.resolve(rfqChannels);
      if (path.startsWith("/api/v1/templates/library")) return Promise.resolve(templateLibrary);
      return Promise.reject(new Error(path));
    });

    render(wrap(<RfqChannelPanel rfqId="r1" />));
    expect(await screen.findByText("Nile Steel")).toBeTruthy();
    expect(screen.getAllByText("Supplier preference").length).toBeGreaterThan(0);
    expect(screen.getByText("Replied")).toBeTruthy();
    expect(screen.getByLabelText("Channel override")).toBeTruthy();
  });

  it("overrides the channel for one supplier", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path.endsWith("/channels")) return Promise.resolve(rfqChannels);
      return Promise.reject(new Error(path));
    });
    apiMock.put.mockResolvedValue({ ok: true });

    render(wrap(<RfqChannelPanel rfqId="r1" />));
    const select = await screen.findByLabelText("Channel override");
    fireEvent.change(select, { target: { value: "email" } });

    await waitFor(() => expect(apiMock.put).toHaveBeenCalledTimes(1));
    expect(apiMock.put).toHaveBeenCalledWith("/api/v1/rfqs/r1/suppliers/s1/channel", {
      channel: "email",
      reason: "Manual override from the RFQ screen",
    });
  });
});

describe("voice console", () => {
  it("lists calls and shows the transcript with the blocked turn", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path.startsWith("/api/v1/voice/features")) {
        return Promise.resolve({
          features: [{ key: "voice_ai_calls", enabled: true, label_en: "AI calls", label_ar: "مكالمات" }],
          provider: "fake",
          disclosure_required: true,
          recording_requires_consent: true,
        });
      }
      if (path.startsWith("/api/v1/voice/calls?")) return Promise.resolve(voiceCalls);
      if (path.startsWith("/api/v1/voice/calls/")) return Promise.resolve(voiceDetail);
      if (path.startsWith("/api/v1/rfqs?")) return Promise.resolve({ items: [] });
      return Promise.reject(new Error(path));
    });

    render(wrap(<VoiceCallsPage />));
    expect((await screen.findAllByText("Recorded")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Open call" }));

    expect(await screen.findByText("أسعار المنافسين كام؟")).toBeTruthy();
    expect(screen.getByText(/COMPETITOR_DISCLOSURE/)).toBeTruthy();
    expect(screen.getByText(/no competitor data/)).toBeTruthy();
  });
});

describe("assistant", () => {
  it("answers inside the role and shows the blocked code", async () => {
    apiMock.get.mockImplementation((path: string) => {
      if (path.startsWith("/api/v1/assistant/sessions")) return Promise.resolve({ items: [] });
      return Promise.reject(new Error(path));
    });
    apiMock.post.mockResolvedValue(assistantAnswer);

    render(wrap(<AssistantPage />));
    const box = await screen.findByLabelText("Type your question…");
    fireEvent.change(box, { target: { value: "Approve the best offer" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));

    expect(await screen.findByText("I cannot do that.")).toBeTruthy();
    expect(screen.getByText("ASSISTANT_FORBIDDEN_ACTION")).toBeTruthy();
    expect(screen.getByText("RFQ-2026-00001")).toBeTruthy();
    expect(apiMock.post).toHaveBeenCalledWith("/api/v1/assistant/ask", {
      text: "Approve the best offer",
      channel: "text",
      locale: "en",
      session_id: null,
    });
  });
});

describe("template library", () => {
  it("lists templates per channel with their variables", async () => {
    apiMock.get.mockResolvedValue(templateLibrary);
    render(wrap(<TemplatesPage />));

    expect(await screen.findByText("Reminder")).toBeTruthy();
    expect(screen.getByText("Reminder for {{rfq_code}}: deadline {{deadline}}")).toBeTruthy();
    expect(screen.getByText("{{rfq_code}}")).toBeTruthy();
  });

  it("keeps the editor closed to roles that cannot administer templates", async () => {
    apiMock.get.mockResolvedValue(templateLibrary);
    render(wrap(<TemplatesPage />));

    const button = (await screen.findByRole("button", { name: /new version/i })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("title")).toMatch(/permission/i);
  });

  it("saves a new version through the editor", async () => {
    state.permissions = [...state.permissions, "admin:templates"];
    apiMock.get.mockResolvedValue(templateLibrary);
    apiMock.post.mockResolvedValue({ id: "tpl-9", version: 3 });
    render(wrap(<TemplatesPage />));

    fireEvent.click(await screen.findByRole("button", { name: /new version/i }));
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "reminder" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Reminder v3" } });
    fireEvent.change(screen.getByLabelText("Body"), {
      target: { value: "Hello {{supplier_name}}" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(apiMock.post).toHaveBeenCalledWith("/api/v1/templates", {
        code: "reminder",
        channel: "whatsapp",
        locale: "ar",
        name: "Reminder v3",
        subject: "",
        body: "Hello {{supplier_name}}",
      }),
    );
    expect(await screen.findByText("Version 3 saved")).toBeTruthy();
  });
});
