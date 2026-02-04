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

  // Ref for the streaming assistant message index so we can mutate without
  // full state replacement on every content_delta.
  const streamingIdx = useRef<number | null>(null);
  const contentBuffer = useRef("");
  const flushScheduled = useRef(false);

  // Flush buffered content to React state (batched via rAF)
  const flushBuffer = useCallback(() => {
    flushScheduled.current = false;
    const idx = streamingIdx.current;
    if (idx === null) return;
    const text = contentBuffer.current;
    setMessages((prev) => {
      const copy = [...prev];
      const msg = copy[idx];
      if (msg) {
        copy[idx] = { ...msg, content: text };
      }
      return copy;
    });
  }, []);

  const handleEvent = useCallback(
    (event: WSEvent) => {
      switch (event.type) {
        case "session_created":
          setSessionId(event.session_id);
          break;

        case "content_delta": {
          // If no assistant message is being streamed yet, create one
          if (streamingIdx.current === null) {
            const newMsg: ChatMessage = {
              id: nextId(),
              role: "assistant",
              content: event.content,
              isStreaming: true,
              toolCalls: [],
              matches: null,
              isToolRunning: false,
            };
            contentBuffer.current = event.content;
            setMessages((prev) => {
              streamingIdx.current = prev.length;
              return [...prev, newMsg];
            });
          } else {
            // Append to buffer, schedule a flush
            contentBuffer.current += event.content;
            if (!flushScheduled.current) {
              flushScheduled.current = true;
              requestAnimationFrame(flushBuffer);
            }
          }
          break;
        }

        case "tool_call_delta": {
          const toolName = event.tool_call.name;
          if (!toolName) break;

          // Ensure there's an assistant message to attach the tool call to
          if (streamingIdx.current === null) {
            const newMsg: ChatMessage = {
              id: nextId(),
              role: "assistant",
              content: "",
              isStreaming: true,
              toolCalls: [toolName],
              matches: null,
              isToolRunning: true,
            };
            contentBuffer.current = "";
            setMessages((prev) => {
              streamingIdx.current = prev.length;
              return [...prev, newMsg];
            });
          } else {
            setMessages((prev) => {
              const copy = [...prev];
              const idx = streamingIdx.current!;
              const msg = copy[idx];
              if (msg && !msg.toolCalls.includes(toolName)) {
                copy[idx] = {
                  ...msg,
                  toolCalls: [...msg.toolCalls, toolName],
                  isToolRunning: true,
                };
              }
              return copy;
            });
          }
          break;
        }

        case "message_complete": {
          // Flush any remaining content
          if (streamingIdx.current !== null) {
            const idx = streamingIdx.current;
            const finalContent = event.content || contentBuffer.current;
            setMessages((prev) => {
              const copy = [...prev];
              const msg = copy[idx];
              if (msg) {
                copy[idx] = {
                  ...msg,
                  content: finalContent,
                  isStreaming: false,
                };
              }
              return copy;
            });
            contentBuffer.current = "";
          }
          break;
        }

        case "tool_result": {
          if (streamingIdx.current === null) break;
          const idx = streamingIdx.current;

          if (event.tool_name === "find_content" && !event.error) {
            try {
              const parsed = JSON.parse(event.result) as {
                success: boolean;
                matches: ContentMatch[];
              };
              if (parsed.success && parsed.matches.length > 0) {
                setMessages((prev) => {
                  const copy = [...prev];
                  const msg = copy[idx];
                  if (msg) {
                    copy[idx] = {
                      ...msg,
                      matches: parsed.matches,
                      isToolRunning: false,
                    };
                  }
                  return copy;
                });
                break;
              }
            } catch {
              // Fallthrough to clear isToolRunning
            }
          }

          // For all other tools, just clear the running indicator
          setMessages((prev) => {
            const copy = [...prev];
            const msg = copy[idx];
            if (msg) {
              copy[idx] = { ...msg, isToolRunning: false };
            }
            return copy;
          });
          break;
        }

        case "final": {
          // Mark streaming complete
          if (streamingIdx.current !== null) {
            const idx = streamingIdx.current;
            setMessages((prev) => {
              const copy = [...prev];
              const msg = copy[idx];
              if (msg) {
                copy[idx] = { ...msg, isStreaming: false, isToolRunning: false };
              }
              return copy;
            });
          }
          streamingIdx.current = null;
          contentBuffer.current = "";
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
          streamingIdx.current = null;
          contentBuffer.current = "";
          setMessages((prev) => [...prev, errMsg]);
          setIsStreaming(false);
          break;
        }
      }
    },
    [flushBuffer],
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
      streamingIdx.current = null;
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
    streamingIdx.current = null;
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
