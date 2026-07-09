import { useQueryClient } from "@tanstack/react-query";

import { $api } from "~/lib/api/query";
import { asSyncRows, type SyncStatusRow } from "~/lib/api/domain";

/** Polls /sync/status on a slow interval; exposes a manual /sync/trigger. */
export function useSyncStatus() {
  const queryClient = useQueryClient();
  const query = $api.useQuery(
    "get",
    "/api/v1/sync/status",
    {},
    { refetchInterval: 30_000 },
  );
  const triggerMutation = $api.useMutation("post", "/api/v1/sync/trigger");

  const rows: SyncStatusRow[] = asSyncRows(query.data);

  async function trigger(): Promise<void> {
    await triggerMutation.mutateAsync({});
    await queryClient.invalidateQueries();
  }

  return {
    rows,
    isLoading: query.isLoading,
    trigger,
    isTriggering: triggerMutation.isPending,
  };
}
