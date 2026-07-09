import { useQueryClient } from "@tanstack/react-query";

import { $api } from "~/lib/api/query";

/** POST /query → { task_id, status, conversation_id }. */
export function useSendQuery() {
  const queryClient = useQueryClient();
  const mutation = $api.useMutation("post", "/api/v1/query", {
    onSuccess: () => {
      // Re-validate the left-panel list: a new conversation may have appeared.
      void queryClient.invalidateQueries({
        queryKey: $api.queryOptions("get", "/api/v1/conversations").queryKey,
      });
    },
  });

  function send(query: string, conversationId: string) {
    return mutation.mutateAsync({
      body: { query, conversation_id: conversationId },
    });
  }

  return { send, isSending: mutation.isPending };
}
