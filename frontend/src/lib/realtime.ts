import { useEffect, useRef, useState } from "react";
import { session } from "./api";

export type RealtimeEvent = { type: string; payload?: Record<string, unknown> };

const HEARTBEAT_MS = 25_000;
const BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 15_000, 30_000];

/**
 * Subscribes to the backend WebSocket hub for live notifications.
 *
 * The socket is a convenience, not a dependency: polling keeps the UI fresh when it cannot
 * connect, so every failure here is swallowed and retried with capped exponential backoff.
 * The backend closes with 4401/4403 when the token is invalid — retrying that would only spam
 * the console, so the client stops and waits for a new session instead.
 */
export function useRealtime(onEvent: (event: RealtimeEvent) => void): boolean {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let heartbeat: ReturnType<typeof setInterval> | null = null;
    let attempt = 0;
    let token = session.get();

    const stopTimers = () => {
      if (retry) clearTimeout(retry);
      if (heartbeat) clearInterval(heartbeat);
      retry = null;
      heartbeat = null;
    };

    const schedule = () => {
      if (closed) return;
      const delay = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)] ?? 30_000;
      attempt += 1;
      retry = setTimeout(connect, delay + Math.random() * 250);
    };

    function connect() {
      if (closed) return;
      const current = session.get();
      token = current;
      if (!current) {
        // Not signed in yet: try again shortly. No socket without a token.
        schedule();
        return;
      }
      const base =
        import.meta.env.VITE_WS_BASE ??
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
      let next: WebSocket;
      try {
        next = new WebSocket(`${base}/ws?token=${encodeURIComponent(current)}`);
      } catch {
        schedule();
        return;
      }
      socket = next;

      next.onopen = () => {
        attempt = 0;
        setConnected(true);
        heartbeat = setInterval(() => {
          if (next.readyState === WebSocket.OPEN) next.send("ping");
        }, HEARTBEAT_MS);
      };

      next.onmessage = (message) => {
        try {
          handler.current(JSON.parse(String(message.data)) as RealtimeEvent);
        } catch {
          /* ignore malformed frames */
        }
      };

      next.onerror = () => {
        // `onclose` always follows; keeping this handler empty is what stops the browser from
        // logging an uncaught error for an expected reconnect.
        setConnected(false);
      };

      next.onclose = (event) => {
        setConnected(false);
        stopTimers();
        if (closed) return;
        // 4401/4403: the token is invalid or expired. Retry only once a new token exists.
        if (event.code === 4401 || event.code === 4403) {
          setTimeout(() => {
            if (!closed && session.get() !== token) connect();
          }, 5_000);
          return;
        }
        schedule();
      };
    }

    connect();

    // Browsers throttle timers in background tabs; re-check the socket when the tab returns.
    const onVisible = () => {
      if (document.visibilityState === "visible" && (!socket || socket.readyState > WebSocket.OPEN)) {
        stopTimers();
        attempt = 0;
        connect();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      closed = true;
      document.removeEventListener("visibilitychange", onVisible);
      stopTimers();
      socket?.close(1000, "client_unmount");
    };
  }, []);

  return connected;
}
