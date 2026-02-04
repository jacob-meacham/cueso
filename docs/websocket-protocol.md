# WebSocket Protocol

The Cueso backend exposes a WebSocket endpoint at `/ws/chat` for streaming LLM interactions.

## Connection

```
ws://localhost:8483/ws/chat
```

The server accepts the connection and waits for client messages. If `allowed_origins` is configured in `app` settings, the server validates the `Origin` header and closes with code `4003` if disallowed.

## Client Messages

The client sends JSON objects with this schema:

```json
{
  "message": "Play Rick and Morty on Netflix",
  "session_id": "abc-123"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | yes | The user's message |
| `session_id` | string \| null | no | Existing session ID to reuse. If null, a new session is created. |

## Server Events

The server streams events as JSON objects. Each has a `type` field.

### `session_created`

Sent immediately after receiving a client message. Contains the session ID.

```json
{
  "type": "session_created",
  "session_id": "abc-123"
}
```

### `content_delta`

Streamed text chunks from the LLM's response.

```json
{
  "type": "content_delta",
  "content": "Let me search",
  "role": "assistant"
}
```

### `tool_call_delta`

Streamed tool call information. Sent when the LLM invokes a tool.

```json
{
  "type": "tool_call_delta",
  "tool_call": {
    "id": "toolu_abc123",
    "name": "find_content",
    "input": null
  }
}
```

Subsequent deltas for the same tool call include partial JSON:

```json
{
  "type": "tool_call_delta",
  "tool_call": {
    "id": "toolu_abc123",
    "name": "find_content",
    "input_json": "{\"title\": \"Rick"
  }
}
```

### `message_complete`

Marks the end of a single LLM response (before tool execution).

```json
{
  "type": "message_complete",
  "content": "Let me find that for you.",
  "tool_calls": ["find_content"],
  "finish_reason": "tool_use"
}
```

### `tool_result`

Result of executing a tool call.

```json
{
  "type": "tool_result",
  "tool_name": "find_content",
  "tool_call_id": "toolu_abc123",
  "result": "{\"success\": true, \"matches\": [...]}"
}
```

On error:

```json
{
  "type": "tool_result",
  "tool_name": "find_content",
  "tool_call_id": "toolu_abc123",
  "result": "Error: Connection refused",
  "error": true
}
```

### `final`

Marks the end of the full response cycle. Always the last event for a given user message.

```json
{
  "type": "final",
  "content": "I found Rick and Morty on Netflix and Hulu.",
  "tool_calls": [],
  "session_id": "abc-123",
  "iteration_count": 2,
  "paused": false
}
```

| Field | Description |
|-------|-------------|
| `content` | The final text response from the LLM |
| `tool_calls` | Tool names called in the last iteration (empty if none) |
| `session_id` | The session ID |
| `iteration_count` | Total LLM round-trips in this session |
| `paused` | `true` if the loop paused due to `pause_after` config |

### `error`

Sent when an unhandled error occurs.

```json
{
  "type": "error",
  "message": "LLM API key is required. Set llm.api_key in config.yml"
}
```

## Example Flows

### Simple query (no tools)

```
Client: {"message": "What is Cueso?"}
Server: {"type": "session_created", "session_id": "..."}
Server: {"type": "content_delta", "content": "Cueso is", "role": "assistant"}
Server: {"type": "content_delta", "content": " a voice-controlled", "role": "assistant"}
Server: {"type": "message_complete", "content": "Cueso is a voice-controlled...", "tool_calls": [], "finish_reason": "end_turn"}
Server: {"type": "final", "content": "Cueso is a voice-controlled...", "session_id": "...", "iteration_count": 1, "paused": false}
```

### Tool-calling flow

```
Client: {"message": "Play Seinfeld"}
Server: {"type": "session_created", "session_id": "..."}
Server: {"type": "tool_call_delta", "tool_call": {"id": "tc1", "name": "find_content", "input": null}}
Server: {"type": "tool_call_delta", "tool_call": {"id": "tc1", "name": "find_content", "input_json": "{\"title\": \"Seinfeld\"}"}}
Server: {"type": "message_complete", "content": "", "tool_calls": ["find_content"], "finish_reason": "tool_use"}
Server: {"type": "tool_result", "tool_name": "find_content", "tool_call_id": "tc1", "result": "{...}"}
Server: {"type": "final", "content": "", "session_id": "...", "iteration_count": 1, "paused": true}
```

Note: `paused: true` because `find_content` is in the `pause_after` set. The client should display the results and wait for the user to choose a service before sending another message.
