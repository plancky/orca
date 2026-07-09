import { useNavigate } from "react-router";

import { $api } from "~/lib/api/query";

/** Left-panel data source: server-backed conversation list + New-chat. */
export function useConversations() {
  const navigate = useNavigate();
  const query = $api.useQuery("get", "/api/v1/conversations", {});

  function newConversation(): void {
    const id = crypto.randomUUID();
    navigate(`/app/c/${id}`);
  }

  return {
    conversations: query.data ?? [],
    isLoading: query.isLoading,
    newConversation,
  };
}
