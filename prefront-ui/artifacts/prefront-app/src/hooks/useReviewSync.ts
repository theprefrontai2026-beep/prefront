import { useCallback, useEffect, useRef, useState } from "react";

export interface Reviewer {
  id: string;
  name: string;
  color: string;
  focusedRuleId: string | null;
}

export interface ReviewEvent {
  type: "rule_status";
  ruleId: string;
  status: "approved" | "rejected";
  by: string;
  documentId?: string;
}

interface UseReviewSyncOptions {
  onRuleStatus?: (evt: ReviewEvent) => void;
}

const WS_URL = (() => {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/ws/review`;
})();

export function useReviewSync({ onRuleStatus }: UseReviewSyncOptions = {}) {
  const [connected, setConnected] = useState(false);
  const [reviewers, setReviewers] = useState<Reviewer[]>([]);
  const [myId, setMyId] = useState<string | null>(null);
  const ws = useRef<WebSocket | null>(null);
  const onRuleStatusRef = useRef(onRuleStatus);
  useEffect(() => { onRuleStatusRef.current = onRuleStatus; }, [onRuleStatus]);

  const send = useCallback((msg: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(msg));
    }
  }, []);

  const focus = useCallback((ruleId: string | null) => {
    send({ type: "focus", ruleId });
  }, [send]);

  /** Broadcast an approve/reject to co-reviewers. documentId is included so the
   *  server can persist it to the audit log. */
  const broadcastRuleStatus = useCallback((
    ruleId: string,
    status: "approved" | "rejected",
    documentId?: string,
  ) => {
    send({ type: "rule_status", ruleId, status, documentId: documentId ?? null });
  }, [send]);

  const identify = useCallback((name: string) => {
    send({ type: "identify", name });
  }, [send]);

  useEffect(() => {
    let alive = true;
    let retryMs = 1500;
    let timeout: ReturnType<typeof setTimeout>;

    function connect() {
      try {
        const socket = new WebSocket(WS_URL);
        ws.current = socket;

        socket.onopen = () => {
          if (!alive) { socket.close(); return; }
          setConnected(true);
          retryMs = 1500;
        };

        socket.onmessage = (evt) => {
          if (!alive) return;
          try {
            const msg = JSON.parse(evt.data as string);
            if (msg.type === "hello") {
              setMyId(msg.id);
            } else if (msg.type === "presence") {
              setReviewers(msg.reviewers);
            } else if (msg.type === "rule_status") {
              onRuleStatusRef.current?.(msg as ReviewEvent);
            }
          } catch { /* malformed */ }
        };

        socket.onclose = () => {
          if (!alive) return;
          setConnected(false);
          timeout = setTimeout(() => { if (alive) connect(); }, retryMs);
          retryMs = Math.min(retryMs * 2, 30_000);
        };

        socket.onerror = () => { socket.close(); };
      } catch { /* WebSocket not available / blocked */ }
    }

    connect();
    return () => {
      alive = false;
      clearTimeout(timeout);
      ws.current?.close();
    };
  }, []);

  return { connected, reviewers, myId, focus, broadcastRuleStatus, identify };
}
