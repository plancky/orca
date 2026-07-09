import { MessageSquarePlus } from "lucide-react";

import { Button } from "~/components/ui/button";
import { useConversations } from "~/lib/history/useConversations";

export default function AppIndex() {
  const { newConversation } = useConversations();
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-center">
        <MessageSquarePlus className="mx-auto size-10 text-muted-foreground" />
        <h2 className="mt-4 text-lg font-semibold">
          Start a new conversation
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Ask across Gmail, Calendar, and Drive — one question at a time.
        </p>
        <Button className="mt-4" onClick={newConversation}>
          New chat
        </Button>
      </div>
    </div>
  );
}
