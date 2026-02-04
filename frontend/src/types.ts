export type ContentMatch = {
  service_name: string;
  channel_id: number;
  content_id: string;
  source_url: string;
  title: string;
  media_type: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  isStreaming: boolean;
  toolCalls: string[];
  matches: ContentMatch[] | null;
  isToolRunning: boolean;
};

export type WSStatus = "connecting" | "connected" | "disconnected" | "error";

export type WSEvent =
  | { type: "session_created"; session_id: string }
  | { type: "content_delta"; content: string; role: string }
  | {
      type: "tool_call_delta";
      tool_call: { id: string; name: string; input_json?: string };
    }
  | {
      type: "message_complete";
      content: string;
      tool_calls: string[];
      finish_reason: string;
    }
  | {
      type: "tool_result";
      tool_name: string;
      tool_call_id: string;
      result: string;
      error?: boolean;
    }
  | {
      type: "final";
      content: string;
      paused: boolean;
      session_id: string;
      iteration_count: number;
    }
  | { type: "error"; message: string };
