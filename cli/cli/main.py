#!/usr/bin/env python3
"""Main entry point for Cueso CLI frontend."""

import asyncio
import sys

import click

from cli.config import Config
from cli.console_app import ConsoleApp


@click.command()
@click.option('--backend-url', help='Backend server URL (overrides stored config)')
@click.option('--websocket-url', help='WebSocket URL for chat (overrides stored config)')
def main(backend_url: str | None, websocket_url: str | None):
    """Cueso CLI - Voice/Text Controlled Roku System

    Interactive CLI for controlling Roku devices through natural language.

    Examples:
        cueso                           # Start with default configuration
        cueso --backend-url http://192.168.1.100:8000  # Custom backend
        cueso --help                   # Show this help
    """
    try:
        # Build unified config once and pass it everywhere
        cli_config = Config(
            backend_url=backend_url,
            websocket_url=websocket_url,
        )

        # Create and run the console app
        app = ConsoleApp(cli_config)
        asyncio.run(app.run())

    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
