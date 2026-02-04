import { useEffect, useRef } from "react";
import type { ChatMessage, ContentMatch } from "../types";
import type { WSStatus } from "../types";
import MessageBubble from "./MessageBubble";

type Props = {
  messages: ChatMessage[];
  onLaunch: (match: ContentMatch) => void;
  onNewSearch: () => void;
  launching: string | null;
  wsStatus: WSStatus;
};

export default function ChatView({
  messages,
  onLaunch,
  onNewSearch,
  launching,
  wsStatus,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new content
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <header className="flex shrink-0 items-center justify-between border-b border-white/10 px-4 py-3">
        <h1 className="text-lg font-bold text-slate-100">cueso</h1>
        <div className="flex items-center gap-3">
          {/* Connection indicator */}
          <span
            className={`h-2 w-2 rounded-full ${
              wsStatus === "connected"
                ? "bg-green-500"
                : wsStatus === "connecting"
                  ? "animate-pulse bg-yellow-500"
                  : "bg-red-500"
            }`}
            title={wsStatus}
          />
          {messages.length > 0 && (
            <button
              onClick={onNewSearch}
              className="rounded-lg border border-white/10 px-3 py-1 text-xs text-slate-400 transition-colors hover:border-white/20 hover:text-slate-200"
            >
              New Search
            </button>
          )}
        </div>
      </header>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-4 pb-24">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <p className="mb-1 text-lg text-slate-300">
              What do you want to watch?
            </p>
            <p className="text-sm text-slate-500">
              Ask about a show or movie and I'll find it for you.
            </p>
          </div>
        ) : (
          <div className="mx-auto flex max-w-2xl flex-col gap-3">
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                message={msg}
                onLaunch={onLaunch}
                launching={launching}
              />
            ))}
            <div ref={bottomRef} />
          </div>
        )}
      </div>
    </div>
  );
}
