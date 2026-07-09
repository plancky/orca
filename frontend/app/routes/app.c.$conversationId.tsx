import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router";

import { MessageInput } from "~/components/chat/MessageInput";
import { MessageThread } from "~/components/chat/MessageThread";
import { type Decision, useConfirmAction } from "~/lib/chat/useConfirmAction";
import { useConversation } from "~/lib/chat/useConversation";
import { useSendQuery } from "~/lib/chat/useSendQuery";
import { useTaskPolling } from "~/lib/chat/useTaskPolling";
import type { Turn } from "~/lib/chat/types";

export default function ConversationRoute() {
  const params = useParams();
  const convId = params.conversationId ?? "";

  const conv = useConversation(convId);
  const { send } = useSendQuery();
  const { confirm } = useConfirmAction();

  const [taskId, setTaskId] = useState<string | null>(null);
  const [localTurns, setLocalTurns] = useState<Turn[]>([]);
  const [lastQuery, setLastQuery] = useState("");
  const foldedRef = useRef<Set<string>>(new Set());

  const { status, result, progress } = useTaskPolling(taskId);

  // Reset per-conversation session state when switching conversations.
  useEffect(() => {
    setTaskId(null);
    setLocalTurns([]);
    setLastQuery("");
    foldedRef.current = new Set();
  }, [convId]);

  // Fold a completed assistant result into the local thread exactly once per
  // task (ref guard is StrictMode-safe: mutation is synchronous).
  useEffect(() => {
    if (
      status === "success" &&
      result &&
      taskId &&
      !foldedRef.current.has(taskId)
    ) {
      foldedRef.current.add(taskId);
      setLocalTurns((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: result.response,
          actions: result.actions_taken,
        },
      ]);
      setTaskId(null);
    }
  }, [status, result, taskId]);

  const serverTurns: Turn[] = useMemo(() => {
    const msgs = conv.data?.messages ?? [];
    return msgs.map((m) => ({
      id: m.id,
      role: m.role === "user" ? "user" : "assistant",
      content: m.content,
    }));
  }, [conv.data]);

  const turns = [...serverTurns, ...localTurns];
  const busy = taskId !== null && status !== "failed";
  const live = taskId !== null ? { status, result, progress } : null;

  async function onSend(text: string) {
    setLastQuery(text);
    setLocalTurns((prev) => [
      ...prev,
      { id: crypto.randomUUID(), role: "user", content: text },
    ]);
    const res = await send(text, convId);
    setTaskId(res.task_id);
  }

  async function onDecision(actionId: string, decision: Decision) {
    const res = await confirm(convId, actionId, decision);
    setTaskId(res.task_id);
  }

  function onRetry() {
    if (lastQuery) void onSend(lastQuery);
  }

  return (
    <div className="flex h-full flex-col">
      <MessageThread
        turns={turns}
        live={live}
        onDecision={onDecision}
        onRetry={onRetry}
      />
      <MessageInput onSend={onSend} disabled={busy} />
    </div>
  );
}
