"""Main console application for CLI frontend using prompt_toolkit."""

import signal

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout  # type: ignore
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .chat_client import ChatClient
from .completer import CuesoCompleter
from .config import Config
from .session_manager import SessionManager


class ConsoleApp:
    """Main console application using prompt_toolkit for auto-completion."""

    def __init__(self, cli_config: Config):
        self._config = cli_config
        self.console = Console()
        self.session_manager = SessionManager(self._config)
        self.chat_client = ChatClient(self._config)
        self.completer = CuesoCompleter()
        self.running = True

        # Create prompt session with auto-completion
        # Completion menu styling: transparent background, light-blue highlight
        self.prompt_style = Style.from_dict({
            'prompt': 'ansicyan bold',

            # Completion menu base
            "completion-menu": "bg:default",
            "completion-menu.completion": "bg:default fg:#bbbbbb",
            "completion-menu.completion.current": "bg:#5fafff fg:#202020 bold",

            # Help/meta text styling
            "completion-menu.meta": "bg:#202020 fg:#bbbbbb",
            "completion-menu.meta.completion": "bg:#202020 fg:#bbbbbb",
            "completion-menu.meta.completion.current": "bg:#202020 #5fafff",  # light blue when selected

            # Selected text generally
            "selected": "bg:default",
        })

        self.prompt_session = PromptSession(
            completer=self.completer,
            style=self.prompt_style,
            complete_while_typing=True,
            history=None,  # We'll add history later if needed
        )

        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle interrupt signals."""
        self.console.print("\n[yellow]Received interrupt signal. Shutting down...[/yellow]")
        self.running = False

    def _print_banner(self):
        """Print the application banner."""
        banner = Text()
        banner.append("🎬 ", style="bold blue")
        banner.append("Cueso CLI", style="bold white")
        banner.append(" - Voice/Text Controlled Roku System", style="dim")

        panel = Panel(
            banner,
            border_style="blue",
            padding=(1, 2)
        )
        self.console.print(panel)

        # Print help with auto-completion info
        help_text = Text()
        help_text.append("Commands (with auto-completion):\n", style="bold")
        help_text.append("list", style="cyan")
        help_text.append(" - List sessions\n", style="white")
        help_text.append("session [id]", style="cyan")
        help_text.append(" - Switch to session or create new\n", style="white")
        help_text.append("config", style="cyan")
        help_text.append(" - Show/manage configuration\n", style="white")
        help_text.append("exit", style="cyan")
        help_text.append(" - Exit application\n", style="white")
        help_text.append("quit", style="cyan")
        help_text.append(" - Exit application\n", style="white")
        help_text.append("help", style="cyan")
        help_text.append(" - Show this help\n", style="white")
        help_text.append("\n[dim]💡 Type '/' or start typing a command for auto-completion[/dim]", style="dim")
        help_text.append("\n[dim]💡 Use ↑↓ arrows to navigate completion suggestions[/dim]", style="dim")
        help_text.append("\n[dim]Any other input - Send message to current session[/dim]", style="dim")

        help_panel = Panel(help_text, title="Help", border_style="dim")
        self.console.print(help_panel)
        self.console.print()

    async def _handle_command(self, command: str) -> bool:
        """Handle slash commands or direct commands."""

        parts = command.strip().split()
        if not parts:
            return True

        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        if cmd in ["/exit", "/quit"]:
            print_formatted_text(HTML("<ansiyellow>Exiting...</ansiyellow>"))
            return False

        elif cmd == "/list":
            result = await self.session_manager.list_sessions()
            if "error" in result:
                print_formatted_text(HTML(f"<ansired>{result['error']}</ansired>"))
            else:
                count = result.get("count", 0)
                sessions = result.get("sessions", [])
                current = result.get("current")
                if count == 0:
                    print_formatted_text(HTML("<ansiyellow>No active sessions found.</ansiyellow>"))
                else:
                    print_formatted_text(HTML(f"<b>Active Sessions ({count}):</b>"))
                    for idx, sid in enumerate(sessions, 1):
                        marker = " → " if sid == current else "   "
                        print_formatted_text(f"{marker}{idx}. {sid}")
                    if current:
                        print_formatted_text(HTML(f"<ansigreen>Current session: {current}</ansigreen>"))
                    else:
                        print_formatted_text(HTML("<ansiyellow>No current session</ansiyellow>"))

        elif cmd == "/session":
            if args:
                session_id = args[0]
                sid = await self.session_manager.create_session(session_id)
                # Connect to the session
                await self.chat_client.connect(sid)
                print_formatted_text(HTML(f"<ansigreen>Switched to session: {sid}</ansigreen>"))
            else:
                sid = await self.session_manager.create_session()
                # Connect to the new session
                await self.chat_client.connect(sid)
                print_formatted_text(HTML(f"<ansigreen>Created new session: {sid}</ansigreen>"))

        elif cmd == "/session-delete":
            if not args:
                print_formatted_text(HTML("<ansired>Usage: /session-delete &lt;session_id&gt;</ansired>"))
            else:
                ok, msg = await self.session_manager.delete_session(args[0])
                color = "ansigreen" if ok else "ansired"
                print_formatted_text(HTML(f"<{color}>{msg}</{color}>"))

        elif cmd == "/session-reset":
            if not args:
                print_formatted_text(HTML("<ansired>Usage: /session-reset &lt;session_id&gt;</ansired>"))
            else:
                ok, msg = await self.session_manager.reset_session(args[0])
                color = "ansigreen" if ok else "ansired"
                print_formatted_text(HTML(f"<{color}>{msg}</{color}>"))

        elif cmd == "/config":
            if args:
                if len(args) >= 2:
                    key, value = args[0], " ".join(args[1:])
                    if key in ["backend_url", "websocket_url", "show_timestamps", "default_session_name"]:
                        # Try to convert value to appropriate type
                        if key in ["show_timestamps"]:
                            if value.lower() in ["true", "1", "yes"]:
                                value = True
                            elif value.lower() in ["false", "0", "no"]:
                                value = False
                            else:
                                print_formatted_text(HTML("<ansired>Error: show_timestamps must be true/false</ansired>"))
                                return True

                        # Update the injected config and persist
                        self._config.set_value(key, value)
                        print_formatted_text(HTML(f"<ansigreen>Set {key} = {value}</ansigreen>"))
                    else:
                        print_formatted_text(HTML(f"<ansired>Unknown config key: {key}</ansired>"))
                        print_formatted_text(HTML("<ansiyellow>Available keys: backend_url, websocket_url, show_timestamps, default_session_name</ansiyellow>"))
                else:
                    print_formatted_text(HTML("<ansired>Usage: config &lt;key&gt; &lt;value&gt;</ansired>"))
            else:
                # Show current config
                data = self._config.to_dict()
                if data:
                    print_formatted_text("Current Configuration:")
                    for key, value in data.items():
                        print_formatted_text(f"  {key} = {value}")
                else:
                    print_formatted_text("No configuration stored. Using defaults.")

        elif cmd == "/help":
            self._print_banner()

        else:
            print_formatted_text(HTML(f"<ansired>Unknown command: {cmd}</ansired>"))
            print_formatted_text(HTML("<ansiyellow>Type 'help' for available commands</ansiyellow>"))

        return True

    async def _handle_message(self, message: str) -> None:
        """Handle a regular message (not a command)."""
        # If no active session, create one automatically
        if not self.session_manager.get_current_session_id():
            session_id = await self.session_manager.create_session()
            await self.chat_client.connect(session_id)

        # Send the message to the backend
        await self.chat_client.send_message(
            message,
            self.session_manager.get_current_session_id()
        )

    async def run(self):
        """Run the main application."""
        # Print banner
        self._print_banner()

        # Start default session
        print_formatted_text(HTML("<ansiblue>Starting default session...</ansiblue>"))
        session_id = await self.session_manager.create_session()
        await self.chat_client.connect(session_id)

        print_formatted_text(HTML("\n<ansigreen>Ready! Type your message or / for commands.</ansigreen>\n"))

        try:
            # Use patch_stdout to prevent prompt_toolkit from interfering with Rich output
            with patch_stdout():
                while self.running:
                    try:
                        # Get user input with auto-completion
                        user_input = await self.prompt_session.prompt_async(
                            HTML("<prompt>› </prompt>"),
                            completer=self.completer,
                        )

                        user_input = user_input.strip()

                        if not user_input:
                            continue

                        # Check if it's a command (starts with slash or is a known command)
                        if (user_input.startswith("/") or
                            user_input.split()[0].lower() in self.completer.get_commands()):
                            should_continue = await self._handle_command(user_input)
                            if not should_continue:
                                break
                        else:
                            # Handle as regular message
                            await self._handle_message(user_input)

                    except KeyboardInterrupt:
                        print_formatted_text(HTML("\n<ansiyellow>Interrupted by user.</ansiyellow>"))
                        break
                    except EOFError:
                        print_formatted_text(HTML("\n<ansiyellow>End of input.</ansiyellow>"))
                        break
                    except Exception as e:
                        print_formatted_text(HTML(f"<ansired>Unexpected error: {e}</ansired>"))

        finally:
            # Cleanup
            await self._cleanup()

    async def _cleanup(self):
        """Clean up resources."""
        # Disconnect chat client
        await self.chat_client.disconnect()

        # Close session manager
        await self.session_manager.close()

        print_formatted_text(HTML("<ansigreen>Goodbye!</ansigreen>"))


async def main():
    """Main entry point."""
    app = ConsoleApp()
    await app.run()
