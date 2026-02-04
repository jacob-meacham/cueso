import type { ChatMessage, ContentMatch } from "../types";
import ContentCards from "./ContentCards";

type Props = {
  message: ChatMessage;
  onLaunch: (match: ContentMatch) => void;
  launching: string | null;
};

export default function MessageBubble({ message, onLaunch, launching }: Props) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 ${
          isUser
            ? "bg-indigo-950 text-slate-100"
            : "bg-slate-800 text-slate-200"
        }`}
      >
        {/* Message text */}
        {message.content && (
          <p className="whitespace-pre-wrap text-sm leading-relaxed">
            {message.content}
            {message.isStreaming && (
              <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-slate-400" />
            )}
          </p>
        )}

        {/* Tool running indicator */}
        {message.isToolRunning && (
          <p className="mt-1 text-xs text-slate-400 animate-pulse">
            Searching streaming services...
          </p>
        )}

        {/* Content cards */}
        {message.matches && message.matches.length > 0 && (
          <ContentCards
            matches={message.matches}
            onLaunch={onLaunch}
            launching={launching}
          />
        )}
      </div>
    </div>
  );
}
