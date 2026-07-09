import { Bot, User as UserIcon } from "lucide-react";
import { Streamdown } from "streamdown";

import { Avatar, AvatarFallback } from "~/components/ui/avatar";
import { Button } from "~/components/ui/button";
import { Card } from "~/components/ui/card";
import { ScrollArea } from "~/components/ui/scroll-area";
import type { TaskProgress, TaskResult } from "~/lib/api/domain";
import type { Decision } from "~/lib/chat/useConfirmAction";
import type { Turn } from "~/lib/chat/types";
import { PendingActionCard } from "./PendingActionCard";
import { ProgressTrace } from "./ProgressTrace";

const markdownComponents = {
  a: (props: React.ComponentProps<"a">) => (
    <a {...props} target="_blank" rel="noreferrer" />
  ),
};

export interface LiveState {
  status: string | undefined;
  result: TaskResult | null;
  progress: TaskProgress | null;
}

function TurnBubble({ turn }: { turn: Turn }) {
  const isUser = turn.role === "user";
  return (
    <div className={isUser ? "flex justify-end" : "flex justify-start"}>
      <div
        className={`flex max-w-[80%] gap-2 ${isUser ? "flex-row-reverse" : ""}`}
      >
        <Avatar className="size-8 shrink-0">
          <AvatarFallback>
            {isUser ? (
              <UserIcon className="size-4" />
            ) : (
              <Bot className="size-4" />
            )}
          </AvatarFallback>
        </Avatar>
        <Card
          className={`gap-1 px-3 py-2 text-sm ${
            isUser ? "bg-primary text-primary-foreground" : ""
          }`}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap">{turn.content}</div>
          ) : (
            <Streamdown
              className="max-w-none text-sm [&_pre]:overflow-x-auto"
              components={markdownComponents}
            >
              {turn.content}
            </Streamdown>
          )}
          {turn.actions && turn.actions.length > 0 ? (
            <div className="mt-1 text-xs opacity-70">
              {turn.actions.map((a, i) => (
                <span key={`${a.tool}-${i}`} className="mr-2">
                  ✓ {a.tool}
                </span>
              ))}
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
}

function AssistantShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-start">
      <div className="flex max-w-[80%] gap-2">
        <Avatar className="size-8 shrink-0">
          <AvatarFallback>
            <Bot className="size-4" />
          </AvatarFallback>
        </Avatar>
        <div className="flex flex-col gap-2">{children}</div>
      </div>
    </div>
  );
}

function LiveBubble({
  live,
  onDecision,
  onRetry,
}: {
  live: LiveState;
  onDecision: (actionId: string, decision: Decision) => void;
  onRetry: () => void;
}) {
  if (live.status === "queued" || live.status === "running") {
    return (
      <AssistantShell>
        <Card className="px-3 py-2">
          <ProgressTrace progress={live.progress} />
        </Card>
      </AssistantShell>
    );
  }
  if (live.status === "awaiting_confirmation") {
    return (
      <AssistantShell>
        {live.result?.response ? (
          <Card className="px-3 py-2 text-sm">
            <Streamdown
              className="max-w-none text-sm [&_pre]:overflow-x-auto"
              components={markdownComponents}
            >
              {live.result.response}
            </Streamdown>
          </Card>
        ) : null}
        {(live.result?.pending_actions ?? []).map((a) => (
          <PendingActionCard
            key={a.action_id}
            action={a}
            onDecision={onDecision}
          />
        ))}
      </AssistantShell>
    );
  }
  if (live.status === "failed") {
    return (
      <AssistantShell>
        <Card className="flex flex-col gap-2 px-3 py-2 text-sm">
          <span className="text-destructive">
            Something went wrong with that request.
          </span>
          <Button
            size="sm"
            variant="outline"
            className="self-start"
            onClick={onRetry}
          >
            Retry
          </Button>
        </Card>
      </AssistantShell>
    );
  }
  return null;
}

export function MessageThread({
  turns,
  live,
  onDecision,
  onRetry,
}: {
  turns: Turn[];
  live: LiveState | null;
  onDecision: (actionId: string, decision: Decision) => void;
  onRetry: () => void;
}) {
  const showLive = live && live.status && live.status !== "success";
  return (
    <ScrollArea className="flex-1">
      <div className="mx-auto flex max-w-2xl flex-col gap-4 p-4">
        {turns.length === 0 && !showLive ? (
          <p className="pt-8 text-center text-sm text-muted-foreground">
            Ask something to get started.
          </p>
        ) : null}
        {turns.map((t) => (
          <TurnBubble key={t.id} turn={t} />
        ))}
        {showLive ? (
          <LiveBubble live={live} onDecision={onDecision} onRetry={onRetry} />
        ) : null}
      </div>
    </ScrollArea>
  );
}
