"""Command auto-completion for the CLI."""

from collections.abc import Iterable

from prompt_toolkit.completion import Completer, Completion


class CuesoCompleter(Completer):
    """Auto-completer for Cueso CLI commands."""

    def __init__(self):
        self.commands = [
            "/list",
            "/session",
            "/session-delete",
            "/session-reset",
            "/config",
            "/exit",
            "/quit",
            "/help",
        ]
        self.descriptions = {
            "/list": "List sessions",
            "/session": "Switch/create a session",
            "/session-delete": "Delete a session",
            "/session-reset": "Reset a session",
            "/config": "Show/manage configuration",
            "/exit": "Exit the application",
            "/quit": "Exit the application",
            "/help": "Show help",
        }

    def get_completions(self, document, complete_event) -> Iterable[Completion]:
        """Get completions for the current input.

        Rules:
        - Only complete slash commands (input must begin with '/').
        - Keep the slash in the buffer and menu.
        - Allow progressive typing: '/c' → '/co' → '/conf' → '/config'.
        """
        text = document.text_before_cursor

        # Only offer completions if the line begins with '/'
        if not text.lstrip().startswith('/'):
            return

        word = document.get_word_before_cursor()
        for cmd in self.commands:
            if cmd.startswith(text):
                insertion = cmd
                desc = self.descriptions.get(cmd, "")
                yield Completion(
                    insertion,
                    start_position=-len(word)-1,
                    display=cmd,
                    display_meta=desc,
                )

    def get_commands(self) -> list[str]:
        """Get the list of available commands."""
        return self.commands.copy()

    def add_command(self, command: str):
        """Add a new command to the completer."""
        if command not in self.commands:
            self.commands.append(command)

    def remove_command(self, command: str):
        """Remove a command from the completer."""
        if command in self.commands:
            self.commands.remove(command)
