import { Badge } from "~/components/ui/badge";
import { Skeleton } from "~/components/ui/skeleton";
import type { TaskProgress } from "~/lib/api/domain";

export function ProgressTrace({ progress }: { progress: TaskProgress | null }) {
  const node = typeof progress?.node === "string" ? progress.node : null;
  return (
    <div className="flex items-center gap-2">
      {node ? (
        <Badge variant="secondary">{node}</Badge>
      ) : (
        <Skeleton className="h-5 w-24" />
      )}
      <span className="text-xs text-muted-foreground">working…</span>
    </div>
  );
}
