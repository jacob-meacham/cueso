"""WebSocket chat client for CLI frontend."""

import asyncio
import json
import logging

import websockets
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.shortcuts import print_formatted_text

from .config import Config


class ChatClient:
    """WebSocket chat client for communicating with the backend."""

    def __init__(self, cli_config: Config):
        self._config = cli_config
        self.websocket: websockets.WebSocketServerProtocol | None = None
        self.is_connected = False
        self._logger = logging.getLogger("cueso.cli.chat")


    async def connect(self, session_id: str | None = None) -> bool:
        """Connect to the WebSocket endpoint.

        Only establishes the WebSocket connection.  The backend creates / joins
        a session when the first real user message arrives (with session_id in
        the payload), so no greeting is sent here.
        """
        try:
            print_formatted_text(HTML("<ansiblue>Connecting to backend...</ansiblue>"))

            websocket_url = self._config.get_websocket_url()
            self.websocket = await websockets.connect(websocket_url)
            self.is_connected = True

            print_formatted_text(HTML("<ansigreen>Connected to backend!</ansigreen>"))
            return True

        except Exception as e:
            print_formatted_text(HTML(f"<ansired>Failed to connect: {e}</ansired>"))
            self.is_connected = False
            return False

    async def _send_message(self, message: str, session_id: str | None = None) -> None:
        """Send a message to the backend."""
        if not self.websocket or not self.is_connected:
            raise ConnectionError("Not connected to backend")
        payload = {
            "message": message,
            "session_id": session_id
        }

        await self.websocket.send(json.dumps(payload))
        self._logger.debug("Sent to WS: %s", payload)

    async def send_message(self, message: str, session_id: str | None = None) -> None:
        """Send a message and stream the response."""
        if not self.is_connected:
            print_formatted_text(HTML("<ansired>Not connected to backend. Use /session to connect.</ansired>"))
            return
        try:
            # Send the message
            await self._send_message(message, session_id)

            # Display user message
            print_formatted_text(HTML("<ansicyan><b>user</b></ansicyan>"))
            print_formatted_text(message)

            # Stream the response
            await self._stream_response()

        except Exception as e:
            print_formatted_text(HTML(f"<ansired>Error sending message: {e}</ansired>"))

    async def _stream_response(self) -> None:
        """Stream the response from the backend."""
        if not self.websocket:
            return
        try:
            # Stream loop
            content_buffer = ""
            printed_final = False
            assistant_label_printed = False
            assistant_streaming = False
            tools_announced: set[str] = set()
            while True:
                    try:
                        # Receive message from backend
                        message = await asyncio.wait_for(self.websocket.recv(), timeout=1.0)
                        data = json.loads(message)
                        self._logger.debug("WS event: %s", data)

                        event_type = data.get("type")

                        if event_type == "session_created":
                            session_id = data.get("session_id")
                            print_formatted_text(HTML(f"<ansigray>Session ID: {session_id}</ansigray>"))

                        elif event_type == "thinking":
                            thinking = data.get("content", "")
                            print_formatted_text(HTML(f"<ansigray>{thinking}</ansigray>"))

                        elif event_type == "tool_call":
                            tool_name = data.get("tool_name", "")
                            tool_args = data.get("tool_arguments", {})
                            print_formatted_text(HTML(f"<ansiyellow>🔧 Using tool: {tool_name}</ansiyellow>"))
                            if tool_args:
                                pretty = json.dumps(tool_args, indent=2)
                                print_formatted_text(HTML(f"<ansigray>Arguments:</ansigray> {pretty}"))

                        elif event_type == "tool_result":
                            result = data.get("result", "")
                            print_formatted_text(HTML(f"<ansigreen>✅ Tool result: {result}</ansigreen>"))

                        elif event_type == "content":
                            content = data.get("content", "")
                            if not assistant_label_printed:
                                print_formatted_text(HTML("<ansimagenta><b>cueso</b></ansimagenta>"))
                                assistant_label_printed = True
                            print_formatted_text(content)

                        # New streaming events from backend
                        elif event_type == "content_delta":
                            delta = data.get("content", "")
                            content_buffer += delta
                            if not assistant_label_printed:
                                print_formatted_text(HTML("<ansimagenta><b>cueso</b></ansimagenta>"))
                                assistant_label_printed = True
                            assistant_streaming = True
                            # Print incrementally without newline
                            print_formatted_text(delta, end="", flush=True)
                        elif event_type == "message_complete":
                            # Prefer server-provided content when present
                            final_content = data.get("content") or content_buffer
                            if assistant_streaming:
                                # finish the streaming line
                                print_formatted_text("")
                                assistant_streaming = False
                                printed_final = True
                            elif final_content:
                                if not assistant_label_printed:
                                    print_formatted_text(HTML("<ansimagenta><b>cueso</b></ansimagenta>"))
                                    assistant_label_printed = True
                                print_formatted_text(final_content)
                                printed_final = True
                            content_buffer = ""
                            tools_announced.clear()
                        elif event_type == "tool_call_delta":
                            tc = data.get("tool_call", {})
                            name = tc.get("name", "")
                            if name and name not in tools_announced:
                                tools_announced.add(name)
                                print_formatted_text(HTML(f"<ansiyellow>🔧 Using tool: {name}</ansiyellow>"))

                        elif event_type == "tool_result":
                            tool_name = data.get("tool_name", "")
                            result_text = data.get("result", "")
                            is_error = data.get("error", False)
                            if is_error:
                                print_formatted_text(
                                    HTML(f"<ansired>  ✗ {tool_name}: {result_text}</ansired>")
                                )
                            elif tool_name == "find_content":
                                self._render_find_content(result_text)
                            else:
                                print_formatted_text(
                                    HTML(f"<ansigreen>  ✓ {tool_name}</ansigreen>")
                                )
                                self._logger.debug("Tool result for %s: %s", tool_name, result_text[:200])

                        elif event_type == "final":
                            # Final response, break out of streaming
                            if assistant_streaming:
                                print_formatted_text("")
                                assistant_streaming = False
                            if not printed_final and content_buffer:
                                if not assistant_label_printed:
                                    print_formatted_text(HTML("<ansimagenta><b>cueso</b></ansimagenta>"))
                                    assistant_label_printed = True
                                print_formatted_text(content_buffer)
                                content_buffer = ""
                                printed_final = True
                            break

                        elif event_type == "error":
                            error = data.get("message", "Unknown error")
                            print_formatted_text(HTML(f"<ansired>❌ Error: {error}</ansired>"))
                            break

                    except TimeoutError:
                        # No message received within timeout, continue
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        print_formatted_text(HTML("<ansired>Connection to backend closed.</ansired>"))
                        self.is_connected = False
                        break

        except Exception as e:
            print_formatted_text(HTML(f"<ansired>Error streaming response: {e}</ansired>"))

    async def disconnect(self) -> None:
        """Disconnect from the backend."""
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            self.is_connected = False
            print_formatted_text(HTML("<ansiyellow>Disconnected from backend.</ansiyellow>"))

    def _render_find_content(self, result_text: str) -> None:
        """Render find_content results as a formatted list of streaming matches."""
        try:
            parsed = json.loads(result_text)
        except json.JSONDecodeError:
            print_formatted_text(HTML(f"<ansigreen>  ✓ find_content</ansigreen>"))
            return

        matches = parsed.get("matches", [])
        if not matches:
            msg = parsed.get("message", "No matches found")
            print_formatted_text(HTML(f"<ansiyellow>  ⚠ {msg}</ansiyellow>"))
            return

        print_formatted_text(HTML(f"<ansigreen>  Found on {len(matches)} service(s):</ansigreen>"))
        for i, match in enumerate(matches, 1):
            service = match.get("service_name", "unknown")
            content_id = match.get("content_id", "")
            channel_id = match.get("channel_id", "")
            media_type = match.get("media_type", "")
            print_formatted_text(
                HTML(
                    f"<ansigreen>    {i}. </ansigreen>"
                    f"<b>{service}</b>"
                    f"<ansigray>  (channel={channel_id} content={content_id} type={media_type})</ansigray>"
                )
            )

    # Note: attribute is_connected is used directly; helper method removed to avoid name clash
