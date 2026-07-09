import { useQueryClient } from "@tanstack/react-query";

import { $api } from "~/lib/api/query";

export type Decision = "approved" | "denied";

/**
 * Approve/Deny a write-gated action: POST /query with a `confirm` block →
 * a NEW task_id (parent_task_id set) that the caller polls the same way.
 */
export function useConfirmAction() {
  const queryClient = useQueryClient();
  const mutation = $api.useMutation("post", "/api/v1/query", {
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: $api.queryOptions("get", "/api/v1/conversations").queryKey,
      });
    },
  });

  function confirm(
    conversationId: string,
    actionId: string,
    decision: Decision,
  ) {
    return mutation.mutateAsync({
      body: {
        query: "",
        conversation_id: conversationId,
        confirm: { action_id: actionId, decision },
      },
    });
  }

  return { confirm, isConfirming: mutation.isPending };
}
