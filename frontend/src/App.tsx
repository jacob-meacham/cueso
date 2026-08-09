import { useCallback, useRef, useState } from "react";
import ChatView from "./components/ChatView";
import InputBar from "./components/InputBar";
import { API_URL, WS_URL } from "./constants";
import { useSpeechRecognition } from "./hooks/useSpeechRecognition";
import { useWebSocket } from "./hooks/useWebSocket";
import type { ChatMessage, ContentMatch, WSEvent } from "./types";

let msgCounter = 0;
function nextId(): string {
  return `msg-${++msgCounter}-${Date.now()}`;
}

export default function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [launching, setLaunching] = useState<string | null>(null);

  // Identity of the assistant message currently being streamed. Assigned
  // SYNCHRONOUSLY before any setState so a burst of WS events processed
  // ahead of a React commit still targets one bubble (splitting bug).
  const streamingId = useRef<string | null>(null);
  // Full response text, accumulated ACROSS tool-loop iterations — never
  // reset at message_complete, or later iterations replace earlier text.
  const contentBuffer = useRef("");
  const separatorPending = useRef(false);
  const flushScheduled = useRef(false);

  // Update the streaming assistant message by id.
  const updateStreaming = useCallback((patch: (msg: ChatMessage) => ChatMessage) => {
    const id = streamingId.current;
    if (id === null) return;
    setMessages((prev) => prev.map((m) => (m.id === id ? patch(m) : m)));
  }, []);

  // Create the streaming assistant message if none exists; returns its id.
  const ensureStreaming = useCallback((initial?: Partial<ChatMessage>) => {
    if (streamingId.current !== null) return streamingId.current;
    const id = nextId();
    streamingId.current = id;
    const newMsg: ChatMessage = {
      id,
      role: "assistant",
      content: "",
      isStreaming: true,
      toolCalls: [],
      matches: null,
      isToolRunning: false,
      ...initial,
    };
    setMessages((prev) => [...prev, newMsg]);
    return id;
  }, []);

  // Flush buffered content to React state (batched via rAF)
  const flushBuffer = useCallback(() => {
    flushScheduled.current = false;
    const text = contentBuffer.current;
    updateStreaming((msg) => ({ ...msg, content: text }));
  }, [updateStreaming]);

  const handleEvent = useCallback(
    (event: WSEvent) => {
      switch (event.type) {
        case "session_created":
          setSessionId(event.session_id);
          break;

        case "content_delta": {
          // Paragraph break between tool-loop iterations
          if (separatorPending.current && contentBuffer.current) {
            contentBuffer.current += "\n\n";
          }
          separatorPending.current = false;
          contentBuffer.current += event.content;
          ensureStreaming({ content: contentBuffer.current });
          if (!flushScheduled.current) {
            flushScheduled.current = true;
            requestAnimationFrame(flushBuffer);
          }
          break;
        }

        case "tool_call_delta": {
          const toolName = event.tool_call.name;
          if (!toolName) break;
          ensureStreaming({ toolCalls: [toolName], isToolRunning: true });
          updateStreaming((msg) =>
            msg.toolCalls.includes(toolName)
              ? { ...msg, isToolRunning: true }
              : { ...msg, toolCalls: [...msg.toolCalls, toolName], isToolRunning: true },
          );
          break;
        }

        case "message_complete": {
          const finalContent = contentBuffer.current || event.content;
          updateStreaming((msg) => ({ ...msg, content: finalContent, isStreaming: false }));
          break;
        }

        case "tool_result": {
          separatorPending.current = true;

          if (event.tool_name === "find_content" && !event.error) {
            try {
              const parsed = JSON.parse(event.result) as {
                success: boolean;
                matches: ContentMatch[];
              };
              if (parsed.success && parsed.matches.length > 0) {
                updateStreaming((msg) => ({
                  ...msg,
                  matches: parsed.matches,
                  isToolRunning: false,
                }));
                break;
              }
            } catch {
              // Fallthrough to clear isToolRunning
            }
          }

          updateStreaming((msg) => ({ ...msg, isToolRunning: false }));
          break;
        }

        case "final": {
          updateStreaming((msg) => ({
            ...msg,
            content: contentBuffer.current || msg.content,
            isStreaming: false,
            isToolRunning: false,
          }));
          streamingId.current = null;
          contentBuffer.current = "";
          separatorPending.current = false;
          setIsStreaming(false);
          break;
        }

        case "error": {
          // Show error as an assistant message
          const errMsg: ChatMessage = {
            id: nextId(),
            role: "assistant",
            content: `Error: ${event.message}`,
            isStreaming: false,
            toolCalls: [],
            matches: null,
            isToolRunning: false,
          };
          streamingId.current = null;
          contentBuffer.current = "";
          separatorPending.current = false;
          setMessages((prev) => [...prev, errMsg]);
          setIsStreaming(false);
          break;
        }
      }
    },
    [ensureStreaming, updateStreaming, flushBuffer],
  );

  const { send, status: wsStatus } = useWebSocket(WS_URL, {
    onEvent: handleEvent,
  });

  const {
    transcript,
    isListening,
    isSupported: isVoiceSupported,
    startListening,
    stopListening,
    resetTranscript,
  } = useSpeechRecognition();

  const handleSend = useCallback(
    (text: string) => {
      // Add user message
      const userMsg: ChatMessage = {
        id: nextId(),
        role: "user",
        content: text,
        isStreaming: false,
        toolCalls: [],
        matches: null,
        isToolRunning: false,
      };
      setMessages((prev) => [...prev, userMsg]);
      setIsStreaming(true);
      streamingId.current = null;
      separatorPending.current = false;
      contentBuffer.current = "";
      send(text, sessionId);
    },
    [send, sessionId],
  );

  const handleLaunch = useCallback(
    async (match: ContentMatch) => {
      setLaunching(match.content_id);
      try {
        const params = new URLSearchParams({
          channel_id: String(match.channel_id),
          content_id: match.content_id,
          media_type: match.media_type,
        });
        if (match.resume_position_ticks != null) {
          params.set("resume_position_ticks", String(match.resume_position_ticks));
        }
        const res = await fetch(`${API_URL}/roku/launch?${params}`, {
          method: "POST",
        });
        const data = (await res.json()) as {
          success: boolean;
          message: string;
        };

        // Add a confirmation message
        const confirmMsg: ChatMessage = {
          id: nextId(),
          role: "assistant",
          content: data.success
            ? `Launched on your Roku.`
            : `Launch failed: ${data.message}`,
          isStreaming: false,
          toolCalls: [],
          matches: null,
          isToolRunning: false,
        };
        setMessages((prev) => [...prev, confirmMsg]);
      } catch (err) {
        const confirmMsg: ChatMessage = {
          id: nextId(),
          role: "assistant",
          content: `Could not reach Roku: ${err instanceof Error ? err.message : "Unknown error"}`,
          isStreaming: false,
          toolCalls: [],
          matches: null,
          isToolRunning: false,
        };
        setMessages((prev) => [...prev, confirmMsg]);
      } finally {
        setLaunching(null);
      }
    },
    [],
  );

  const handleNewSearch = useCallback(async () => {
    // Reset session on backend if we have one
    if (sessionId) {
      try {
        await fetch(`${API_URL}/chat/sessions/${sessionId}/reset`, {
          method: "POST",
        });
      } catch {
        // Best-effort reset
      }
    }
    setMessages([]);
    setSessionId(null);
    setIsStreaming(false);
    streamingId.current = null;
      separatorPending.current = false;
    contentBuffer.current = "";
  }, [sessionId]);

  const handleMicToggle = useCallback(() => {
    if (isListening) {
      stopListening();
    } else {
      startListening();
    }
  }, [isListening, startListening, stopListening]);

  return (
    <div className="h-full">
      <ChatView
        messages={messages}
        onLaunch={handleLaunch}
        onNewSearch={handleNewSearch}
        launching={launching}
        wsStatus={wsStatus}
      />
      <InputBar
        onSend={handleSend}
        disabled={isStreaming}
        voiceTranscript={transcript}
        isListening={isListening}
        isVoiceSupported={isVoiceSupported}
        onMicToggle={handleMicToggle}
        onResetTranscript={resetTranscript}
      />
    </div>
  );
}
