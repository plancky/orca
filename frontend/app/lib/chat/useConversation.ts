import { $api } from "~/lib/api/query";

/** Server-backed hydration of a conversation's persisted turns (history/reload). */
export function useConversation(conversationId: string) {
  return $api.useQuery(
    "get",
    "/api/v1/conversations/{conversation_id}",
    { params: { path: { conversation_id: conversationId } } },
    { retry: false, enabled: conversationId.length > 0 },
  );
}
