// Narrowing types for the genuinely-untyped API payloads. The backend types
// TaskPublic.result / TaskPublic.progress as `dict[str, Any]` and /sync/status
// as `list[dict]`, which openapi-typescript renders as `{ [k: string]: unknown }`.
// The generated schema stays the source of truth for path/param/status shapes;
// these narrow the loose JSON at the render boundary (parse, don't trust).
import type { components } from "./schema";

export type TaskPublic = components["schemas"]["TaskPublic"];

export type TaskStatus =
  | "queued"
  | "running"
  | "awaiting_confirmation"
  | "success"
  | "failed";

const TERMINAL: ReadonlySet<string> = new Set<TaskStatus>([
  "success",
  "failed",
  "awaiting_confirmation",
]);

export function isTerminal(status: string | undefined | null): boolean {
  return status != null && TERMINAL.has(status);
}

export interface ActionTaken {
  tool: string;
  status: string;
}

export interface PendingAction {
  action_id: string;
  tool: string;
  args: Record<string, unknown>;
  preview?: string;
}

export interface TaskResult {
  response: string;
  actions_taken: ActionTaken[];
  pending_actions: PendingAction[] | null;
}

export interface TaskProgress {
  node?: string;
  [key: string]: unknown;
}

export type ServiceKey = "gmail" | "gcal" | "gdrive";

export interface SyncStatusRow {
  service: ServiceKey;
  last_synced_at: string | null;
  item_count: number;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null;
}

/** Narrow a TaskPublic.result blob to the synthesizer's shape (or null). */
export function asTaskResult(v: unknown): TaskResult | null {
  if (!isObject(v)) return null;
  if (typeof v.response !== "string") return null;
  const actions_taken = Array.isArray(v.actions_taken)
    ? (v.actions_taken as ActionTaken[])
    : [];
  const pending_actions = Array.isArray(v.pending_actions)
    ? (v.pending_actions as PendingAction[])
    : null;
  return { response: v.response, actions_taken, pending_actions };
}

/** Narrow a TaskPublic.progress blob for the progress trace. */
export function asTaskProgress(v: unknown): TaskProgress | null {
  return isObject(v) ? (v as TaskProgress) : null;
}

/** Narrow the /sync/status array (untyped `list[dict]` in the contract). */
export function asSyncRows(v: unknown): SyncStatusRow[] {
  if (!Array.isArray(v)) return [];
  const rows: SyncStatusRow[] = [];
  for (const r of v) {
    if (!isObject(r)) continue;
    const service = r.service;
    if (service !== "gmail" && service !== "gcal" && service !== "gdrive") {
      continue;
    }
    rows.push({
      service,
      last_synced_at:
        typeof r.last_synced_at === "string" ? r.last_synced_at : null,
      item_count: typeof r.item_count === "number" ? r.item_count : 0,
    });
  }
  return rows;
}
