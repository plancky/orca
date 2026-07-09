import { $api } from "~/lib/api/query";

/** POST /query → { task_id, status, conversation_id }. */
export function useSendQuery() {
  const mutation = $api.useMutation("post", "/api/v1/query");

  function send(query: string, conversationId: string) {
    return mutation.mutateAsync({
      body: { query, conversation_id: conversationId },
    });
  }

  return { send, isSending: mutation.isPending };
}
