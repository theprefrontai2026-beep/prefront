import { WebSocket, WebSocketServer } from "ws";
import { IncomingMessage } from "http";
import { logger } from "./logger";
import { db } from "./db";
import { ruleAuditLog } from "@workspace/db";

// ── Reviewer colours (warm palette) ────────────────────────────────────────
const COLORS = ["#8f6443", "#5f6b4d", "#97712f", "#5a7a8b", "#8b5a8b", "#5a8b7a", "#8b6b8b", "#6b8b5a"];
let colorIdx = 0;
const NAMES = ["Alex", "Blake", "Casey", "Drew", "Emery", "Finley", "Gray", "Harper"];
let nameIdx = 0;

function nextColor() { return COLORS[colorIdx++ % COLORS.length]; }
function nextName() { return NAMES[nameIdx++ % NAMES.length]; }
function uid() { return Math.random().toString(36).slice(2, 10); }

// ── Reviewer state ─────────────────────────────────────────────────────────
interface Reviewer {
  id: string;
  name: string;
  color: string;
  focusedRuleId: string | null;
  ws: WebSocket;
}

const clients = new Map<string, Reviewer>();

function presencePayload() {
  return JSON.stringify({
    type: "presence",
    reviewers: Array.from(clients.values()).map(({ id, name, color, focusedRuleId }) => ({
      id, name, color, focusedRuleId,
    })),
  });
}

function broadcast(payload: string, skip?: string) {
  for (const [id, r] of clients) {
    if (id === skip) continue;
    if (r.ws.readyState === WebSocket.OPEN) r.ws.send(payload);
  }
}

function broadcastPresence() {
  const p = presencePayload();
  for (const r of clients.values()) {
    if (r.ws.readyState === WebSocket.OPEN) r.ws.send(p);
  }
}

/** Write an audit entry to the database (best-effort — never throws). */
async function writeAudit(entry: {
  documentId: string;
  ruleKey: string;
  action: string;
  reviewerName: string;
  reviewerColor?: string | null;
  before?: unknown;
  after?: unknown;
  note?: string | null;
}) {
  try {
    await db.insert(ruleAuditLog).values({
      documentId: entry.documentId,
      ruleKey: entry.ruleKey,
      action: entry.action,
      reviewerName: entry.reviewerName,
      reviewerColor: entry.reviewerColor ?? null,
      before: entry.before ?? null,
      after: entry.after ?? null,
      note: entry.note ?? null,
    });
  } catch (err) {
    logger.warn({ err }, "audit write failed (non-fatal)");
  }
}

// ── Hub setup ──────────────────────────────────────────────────────────────
export function attachReviewHub(wss: WebSocketServer) {
  wss.on("connection", (ws: WebSocket, _req: IncomingMessage) => {
    const id = uid();
    const reviewer: Reviewer = {
      id, name: nextName(), color: nextColor(), focusedRuleId: null, ws,
    };
    clients.set(id, reviewer);

    logger.info({ reviewerId: id, name: reviewer.name }, "reviewer connected");

    // Greet the new reviewer
    ws.send(JSON.stringify({ type: "hello", id, name: reviewer.name, color: reviewer.color }));

    // Tell everyone (including newcomer) the current roster
    broadcastPresence();

    ws.on("message", (raw) => {
      let msg: any;
      try { msg = JSON.parse(raw.toString()); } catch { return; }

      if (msg.type === "identify" && typeof msg.name === "string") {
        reviewer.name = msg.name.slice(0, 40) || reviewer.name;
        broadcastPresence();

      } else if (msg.type === "focus") {
        reviewer.focusedRuleId = typeof msg.ruleId === "string" ? msg.ruleId : null;
        broadcastPresence();

      } else if (msg.type === "rule_status") {
        // Relay approval/rejection to every other client
        const relay = JSON.stringify({
          type: "rule_status",
          ruleId: msg.ruleId,
          status: msg.status,
          by: reviewer.name,
          color: reviewer.color,
          documentId: msg.documentId,
        });
        broadcast(relay, id);

        // Persist to audit log (best-effort)
        if (msg.ruleId && msg.status) {
          writeAudit({
            documentId: String(msg.documentId ?? "unknown"),
            ruleKey: String(msg.ruleId),
            action: String(msg.status),
            reviewerName: reviewer.name,
            reviewerColor: reviewer.color,
            before: msg.before ?? null,
            after: msg.after ?? null,
            note: msg.note ?? null,
          });
        }
      }
    });

    ws.on("close", () => {
      clients.delete(id);
      logger.info({ reviewerId: id }, "reviewer disconnected");
      broadcastPresence();
    });

    ws.on("error", (err) => {
      logger.warn({ reviewerId: id, err }, "reviewer ws error");
    });
  });
}
