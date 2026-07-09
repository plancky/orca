import type { ActionTaken } from "~/lib/api/domain";

export interface Turn {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions?: ActionTaken[];
}
