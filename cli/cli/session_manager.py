"""Session management for CLI frontend.

This module intentionally contains no terminal output. It returns data to the caller
so the UI layer (prompt_toolkit) can render consistently.
"""

import uuid

import httpx

from .config import Config


class SessionManager:
    """Manages chat sessions for the CLI."""

    def __init__(self, cli_config: Config):
        self.config = cli_config
        self.current_session_id: str | None = None
        self.http_client = httpx.AsyncClient()

    async def list_sessions(self) -> dict:
        """Fetch all sessions and return structured data.

        Returns a dict like {"sessions": [...], "count": int} or {"error": str}.
        """
        try:
            api_base_url = self.config.get_api_base_url()
            response = await self.http_client.get(f"{api_base_url}/sessions")
            if response.status_code == 200:
                data = response.json()
                return {
                    "sessions": data.get("sessions", []),
                    "count": data.get("count", 0),
                    "current": self.current_session_id,
                }
            return {"error": f"Failed to list sessions: {response.status_code}"}
        except Exception as e:
            return {"error": f"Error listing sessions: {e}"}

    async def create_session(self, session_id: str | None = None) -> str:
        """Create a new session or use existing one."""
        if session_id:
            # Use existing session
            self.current_session_id = session_id
            return session_id
        # Create new session
        new_session_id = str(uuid.uuid4())
        self.current_session_id = new_session_id
        return new_session_id

    async def delete_session(self, session_id: str) -> tuple[bool, str]:
        """Delete a session. Returns (success, message)."""
        try:
            api_base_url = self.config.get_api_base_url()
            response = await self.http_client.delete(f"{api_base_url}/sessions/{session_id}")
            if response.status_code == 200:
                if session_id == self.current_session_id:
                    self.current_session_id = None
                return True, f"Deleted session: {session_id}"
            return False, f"Failed to delete session: {response.status_code}"
        except Exception as e:
            return False, f"Error deleting session: {e}"

    async def reset_session(self, session_id: str) -> tuple[bool, str]:
        """Reset a session. Returns (success, message)."""
        try:
            api_base_url = self.config.get_api_base_url()
            response = await self.http_client.post(f"{api_base_url}/sessions/{session_id}/reset")
            if response.status_code == 200:
                return True, f"Reset session: {session_id}"
            return False, f"Failed to reset session: {response.status_code}"
        except Exception as e:
            return False, f"Error resetting session: {e}"

    def get_current_session_id(self) -> str | None:
        """Get the current session ID."""
        return self.current_session_id

    async def close(self) -> None:
        """Close the session manager."""
        await self.http_client.aclose()
