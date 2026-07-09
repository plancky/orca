import { Plus } from "lucide-react";
import { NavLink } from "react-router";

import { Button } from "~/components/ui/button";
import { ScrollArea } from "~/components/ui/scroll-area";
import { useConversations } from "~/lib/history/useConversations";
import { cn } from "~/lib/utils";

export function ConversationList() {
  const { conversations, newConversation } = useConversations();

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r bg-sidebar">
      <div className="p-2">
        <Button className="w-full justify-start gap-2" onClick={newConversation}>
          <Plus className="size-4" />
          New chat
        </Button>
      </div>
      <ScrollArea className="flex-1">
        <nav className="flex flex-col gap-1 p-2">
          {conversations.map((c) => (
            <NavLink
              key={c.id}
              to={`/app/c/${c.id}`}
              className={({ isActive }) =>
                cn(
                  "rounded-md px-3 py-2 text-sm hover:bg-accent",
                  isActive && "bg-accent font-medium",
                )
              }
            >
              <div className="truncate">{c.title ?? "New conversation"}</div>
              <div className="text-xs text-muted-foreground">
                {new Date(c.updated_at).toLocaleString()}
              </div>
            </NavLink>
          ))}
          {conversations.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              No conversations yet
            </p>
          ) : null}
        </nav>
      </ScrollArea>
    </aside>
  );
}
