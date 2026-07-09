import { $api } from "~/lib/api/query";
import {
  asTaskProgress,
  asTaskResult,
  isTerminal,
  type TaskProgress,
  type TaskPublic,
  type TaskResult,
} from "~/lib/api/domain";

/**
 * Polls GET /tasks/{id} every second until a terminal status, then stops.
 * `result`/`progress` are narrowed through the domain guards (the contract
 * types them as loose objects).
 */
export function useTaskPolling(taskId: string | null) {
  const query = $api.useQuery(
    "get",
    "/api/v1/tasks/{task_id}",
    { params: { path: { task_id: taskId ?? "" } } },
    {
      enabled: taskId !== null,
      refetchInterval: (q) =>
        isTerminal((q.state.data as TaskPublic | undefined)?.status)
          ? false
          : 1000,
    },
  );

  const task = query.data;
  const result: TaskResult | null = asTaskResult(task?.result);
  const progress: TaskProgress | null = asTaskProgress(task?.progress);

  return {
    task,
    status: task?.status,
    result,
    progress,
    isLoading: query.isLoading,
  };
}
