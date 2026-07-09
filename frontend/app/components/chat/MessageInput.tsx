import { Send } from "lucide-react";
import { type KeyboardEvent, useState } from "react";

import { Button } from "~/components/ui/button";
import { Textarea } from "~/components/ui/textarea";

export function MessageInput({
  onSend,
  disabled,
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
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
    <div className="flex items-end gap-2 border-t p-3">
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
  );
}
