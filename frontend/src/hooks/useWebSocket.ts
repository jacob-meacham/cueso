import { useCallback, useEffect, useRef, useState } from "react";
import type { WSEvent, WSStatus } from "../types";

const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

type Options = {
  onEvent: (event: WSEvent) => void;
};

export function useWebSocket(url: string, { onEvent }: Options) {
  const [status, setStatus] = useState<WSStatus>("disconnected");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempt = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const connect = useCallback(() => {
    // Clean up existing connection
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
    }

    setStatus("connecting");
    const ws = new WebSocket(`${url}/ws/chat`);

    ws.onopen = () => {
      setStatus("connected");
      reconnectAttempt.current = 0;
    };

    ws.onmessage = (e: MessageEvent) => {
      try {
        const event = JSON.parse(e.data as string) as WSEvent;
        onEventRef.current(event);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      setStatus("disconnected");
      wsRef.current = null;

      // Exponential backoff reconnect
      const delay = Math.min(
        RECONNECT_BASE_MS * 2 ** reconnectAttempt.current,
        RECONNECT_MAX_MS,
      );
      reconnectAttempt.current += 1;
      reconnectTimer.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      setStatus("error");
      // onclose will fire after onerror, triggering reconnect
    };

    wsRef.current = ws;
  }, [url]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [connect]);

  const send = useCallback(
    (message: string, sessionId?: string | null) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({ message, session_id: sessionId ?? null }),
        );
      }
    },
    [],
  );

  const disconnect = useCallback(() => {
    clearTimeout(reconnectTimer.current);
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus("disconnected");
  }, []);

  return { send, status, disconnect };
}
