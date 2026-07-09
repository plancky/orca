import { LogOut, Plus, Trash2 } from "lucide-react";
import { NavLink, useLocation, useNavigate } from "react-router";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "~/components/ui/alert-dialog";
import { Button } from "~/components/ui/button";
import { ScrollArea } from "~/components/ui/scroll-area";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "~/components/ui/tooltip";
import { useAuth } from "~/lib/auth/useAuth";
import { useConversations } from "~/lib/history/useConversations";
import { cn } from "~/lib/utils";

const TITLE_MAX_LENGTH = 40;

function truncateTitle(title: string) {
  if (title.length <= TITLE_MAX_LENGTH) return title;
  return `${title.slice(0, TITLE_MAX_LENGTH).trimEnd()}...`;
}

export function ConversationList() {
  const { conversations, newConversation, deleteConversation } =
    useConversations();
  const { pathname } = useLocation();
  const { logout } = useAuth();
  const navigate = useNavigate();

  function onLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  async function onDelete(id: string) {
    try {
      await deleteConversation(id);
    } catch {
      toast.error("Could not delete conversation");
    }
  }

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
          {conversations.map((c) => {
            const title = c.title ?? "New conversation";
            const href = `/app/c/${c.id}`;
            const isActive = pathname === href;
            return (
              <div key={c.id} className="group relative">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <NavLink
                      to={href}
                      className={cn(
                        "block min-w-0 rounded-md border-l-2 border-transparent px-3 py-2 pr-8 text-sm hover:bg-sidebar-accent",
                        isActive &&
                          "border-sidebar-primary bg-sidebar-accent font-medium text-sidebar-accent-foreground",
                      )}
                    >
                      <div className="truncate">{truncateTitle(title)}</div>
                      <div className="text-xs text-muted-foreground">
                        {new Date(c.updated_at).toLocaleString()}
                      </div>
                    </NavLink>
                  </TooltipTrigger>
                  <TooltipContent side="right">{title}</TooltipContent>
                </Tooltip>
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label="Delete conversation"
                      className="absolute right-1 top-1/2 -translate-y-1/2 text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100"
                    >
                      <Trash2 />
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Delete conversation?</AlertDialogTitle>
                      <AlertDialogDescription>
                        This permanently deletes &ldquo;{truncateTitle(title)}
                        &rdquo; and all its messages.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Cancel</AlertDialogCancel>
                      <AlertDialogAction
                        variant="destructive"
                        onClick={() => onDelete(c.id)}
                      >
                        Delete
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
            );
          })}
          {conversations.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              No conversations yet
            </p>
          ) : null}
        </nav>
      </ScrollArea>
      <div className="border-t p-2">
        <Button
          variant="ghost"
          className="w-full justify-start gap-2"
          onClick={onLogout}
        >
          <LogOut className="size-4" />
          Logout
        </Button>
      </div>
    </aside>
  );
}
