import { Calendar, HardDrive, Mail } from "lucide-react";
import type { ComponentType } from "react";

import { Badge } from "~/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "~/components/ui/tooltip";
import type { ServiceKey, SyncStatusRow } from "~/lib/api/domain";

const LABELS: Record<ServiceKey, string> = {
  gmail: "Gmail",
  gcal: "Calendar",
  gdrive: "Drive",
};

const ICONS: Record<ServiceKey, ComponentType<{ className?: string }>> = {
  gmail: Mail,
  gcal: Calendar,
  gdrive: HardDrive,
};

type Connection = "connected" | "stale" | "disconnected";

function classify(row: SyncStatusRow | undefined): Connection {
  if (!row || !row.last_synced_at) return "disconnected";
  const ageMs = Date.now() - new Date(row.last_synced_at).getTime();
  return ageMs < 2 * 60 * 60 * 1000 ? "connected" : "stale";
}

export function ServicePill({
  service,
  row,
}: {
  service: ServiceKey;
  row: SyncStatusRow | undefined;
}) {
  const conn = classify(row);
  const Icon = ICONS[service];
  const variant =
    conn === "connected"
      ? "default"
      : conn === "stale"
        ? "secondary"
        : "outline";
  const count = row?.item_count ?? 0;
  const detail =
    conn === "disconnected"
      ? "Not connected"
      : `${count} item${count === 1 ? "" : "s"} · ${
          conn === "stale" ? "sync stale" : "synced"
        }`;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant={variant} className="gap-1">
          <Icon className="size-3" />
          {LABELS[service]}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>{detail}</TooltipContent>
    </Tooltip>
  );
}
