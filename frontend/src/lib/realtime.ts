import { useEffect, useRef, useState } from "react";
import { session } from "./api";

export type RealtimeEvent = { type: string; payload?: Record<string, unknown> };

/**
 * Subscribes to the backend WebSocket hub for live notifications.
 * Falls back to silence (polling still keeps the UI fresh) when the socket cannot connect.
 */
export function useRealtime(onEvent: (event: RealtimeEvent) => void): boolean {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      const token = session.get();
      const base = import.meta.env.VITE_WS_BASE ?? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
      const url = `${base}/api/v1/ws${token ? `?token=${encodeURIComponent(token)}` : ""}`;
      try {
        socket = new WebSocket(url);
      } catch {
        return;
      }
      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 5_000);
      };
      socket.onerror = () => setConnected(false);
      socket.onmessage = (message) => {
        try {
          handler.current(JSON.parse(String(message.data)) as RealtimeEvent);
        } catch {
          /* ignore malformed frames */
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, []);

  return connected;
}
