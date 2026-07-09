import { RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "~/components/ui/button";
import type { ServiceKey, SyncStatusRow } from "~/lib/api/domain";
import { useSyncStatus } from "~/lib/sync/useSyncStatus";
import { ServicePill } from "./ServicePill";

const SERVICES: ServiceKey[] = ["gmail", "gcal", "gdrive"];

export function StatusBar() {
  const { rows, trigger, isTriggering } = useSyncStatus();
  const byService = new Map<ServiceKey, SyncStatusRow>(
    rows.map((r) => [r.service, r]),
  );

  async function onRefresh() {
    try {
      await trigger();
      toast.success("Sync triggered");
    } catch {
      toast.error("Could not trigger sync");
    }
  }

  return (
    <div className="flex items-center justify-end gap-2">
      {SERVICES.map((s) => (
        <ServicePill key={s} service={s} row={byService.get(s)} />
      ))}
      <Button
        variant="ghost"
        size="sm"
        onClick={onRefresh}
        disabled={isTriggering}
        aria-label="Trigger sync"
      >
        <RefreshCw className={isTriggering ? "size-4 animate-spin" : "size-4"} />
      </Button>
    </div>
  );
}
