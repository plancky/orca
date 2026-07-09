import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";

import { $api } from "~/lib/api/query";

/** Left-panel data source: server-backed conversation list + New-chat. */
export function useConversations() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const query = $api.useQuery("get", "/api/v1/conversations", {});
  const deleteMutation = $api.useMutation(
    "delete",
    "/api/v1/conversations/{conversation_id}",
    {
      onSuccess: () => {
        void queryClient.invalidateQueries({
          queryKey: $api.queryOptions("get", "/api/v1/conversations").queryKey,
        });
      },
    },
  );

  function newConversation(): void {
    const id = crypto.randomUUID();
    navigate(`/app/c/${id}`);
  }

  async function deleteConversation(id: string): Promise<void> {
    await deleteMutation.mutateAsync({
      params: { path: { conversation_id: id } },
    });
    // Deleting the open conversation: bounce back to the empty-state view.
    if (window.location.pathname === `/app/c/${id}`) {
      navigate("/app");
    }
  }

  return {
    conversations: query.data ?? [],
    isLoading: query.isLoading,
    newConversation,
    deleteConversation,
    isDeleting: deleteMutation.isPending,
  };
}
