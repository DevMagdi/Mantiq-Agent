import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The socket is a convenience layer, not a dependency: these cases pin the contract the browser
 * relies on — the right URL, a heartbeat, capped reconnection, and *no* retry storm after the
 * server says the token is bad. The old bug was a `ws://localhost:5173/api/v1/ws` URL (wrong
 * path) plus unhandled errors on every failed attempt.
 */

class FakeSocket {
  static instances: FakeSocket[] = [];
  static readonly OPEN = 1;
  readyState = 1;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close(code = 1000, reason = "") {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }
  // helpers used by the tests
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  serverClose(code: number, reason = "") {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }
  message(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

const { sessionMock } = vi.hoisted(() => ({ sessionMock: { token: "jwt-token" as string | null } }));

vi.mock("./api", () => ({
  session: { get: () => sessionMock.token, set: () => undefined, clear: () => undefined },
}));

import { useRealtime } from "./realtime";

describe("useRealtime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // The hook adds up to 250 ms of jitter; pin it so the backoff assertions are exact.
    vi.spyOn(Math, "random").mockReturnValue(0);
    FakeSocket.instances = [];
    sessionMock.token = "jwt-token";
    vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("connects to the same origin on /ws (never the old /api/v1/ws path)", () => {
    renderHook(() => useRealtime(() => undefined));
    expect(FakeSocket.instances).toHaveLength(1);
    const url = new URL(FakeSocket.instances[0]!.url);
    expect(url.pathname).toBe("/ws");
    expect(url.searchParams.get("token")).toBe("jwt-token");
    expect(url.protocol).toBe("ws:");
  });

  it("sends a heartbeat every 25 s and delivers parsed events", () => {
    const received: string[] = [];
    const { result } = renderHook(() => useRealtime((event) => received.push(event.type)));
    const socket = FakeSocket.instances[0]!;

    act(() => socket.open());
    expect(result.current).toBe(true);

    act(() => socket.message({ type: "notification.created", payload: { id: "n1" } }));
    expect(received).toEqual(["notification.created"]);

    act(() => void vi.advanceTimersByTime(25_000));
    expect(socket.sent).toEqual(["ping"]);
    act(() => void vi.advanceTimersByTime(25_000));
    expect(socket.sent).toHaveLength(2);
  });

  it("stops retrying after a 4401 (invalid token) instead of flooding the console", () => {
    const { result } = renderHook(() => useRealtime(() => undefined));
    const socket = FakeSocket.instances[0]!;
    act(() => socket.open());
    act(() => socket.serverClose(4401, "invalid_token"));

    expect(result.current).toBe(false);
    act(() => void vi.advanceTimersByTime(120_000));
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("reconnects with capped backoff after a network drop, then resets the delay", () => {
    renderHook(() => useRealtime(() => undefined));
    act(() => FakeSocket.instances[0]!.open());
    act(() => FakeSocket.instances[0]!.serverClose(1006));

    act(() => void vi.advanceTimersByTime(1_100));
    expect(FakeSocket.instances).toHaveLength(2);
    act(() => FakeSocket.instances[1]!.open());

    act(() => FakeSocket.instances[1]!.serverClose(1006));
    act(() => void vi.advanceTimersByTime(1_100));
    expect(FakeSocket.instances).toHaveLength(3);

    // A successful open resets the attempt counter, so the next drop waits ~1 s again, not 4 s.
    act(() => FakeSocket.instances[2]!.open());
    act(() => FakeSocket.instances[2]!.serverClose(1006));
    act(() => void vi.advanceTimersByTime(1_100));
    expect(FakeSocket.instances).toHaveLength(4);
  });

  it("never calls the network without a token, and cleans every timer on unmount", () => {
    const { unmount } = renderHook(() => useRealtime(() => undefined));
    act(() => FakeSocket.instances[0]!.open());
    unmount();
    expect(FakeSocket.instances[0]!.readyState).toBe(3);

    sessionMock.token = null;
    renderHook(() => useRealtime(() => undefined));
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("survives a malformed frame without throwing", () => {
    const received: string[] = [];
    renderHook(() => useRealtime((event) => received.push(event.type)));
    const socket = FakeSocket.instances[0]!;
    act(() => socket.open());
    expect(() => socket.onmessage?.({ data: "not json" })).not.toThrow();
    expect(received).toEqual([]);
  });
});
