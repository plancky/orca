import { Send } from "lucide-react";
import { type KeyboardEvent, useState } from "react";

import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";

export function MessageInput({
  onSend,
  disabled,
  centered,
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
  centered?: boolean;
}) {
  const [text, setText] = useState("");

  function submit() {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div
      className={
        centered
          ? "flex flex-1 flex-col items-center justify-center p-4"
          : "border-t p-4"
      }
    >
      <div className="mx-auto flex w-full max-w-2xl items-end gap-2 pb-6">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about your Gmail, Calendar, or Drive…"
          disabled={disabled}
          rows={1}
          className="max-h-40 min-h-10 resize-none"
          aria-label="Message"
        />
        <Button
          onClick={submit}
          disabled={disabled || text.trim().length === 0}
          aria-label="Send"
        >
          <Send className="size-4" />
        </Button>
      </div>
    </div>
  );
}
