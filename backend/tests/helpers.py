"""Shared test helpers for the backend test suite."""

from unittest.mock import MagicMock

import httpx


def make_mock_response(
    status_code: int = 200,
    json_data: dict[str, object] | None = None,
) -> MagicMock:
    """Create a mock httpx.Response."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_data or {}
    response.text = ""
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=response,
        )
    else:
        response.raise_for_status.return_value = None
    return response
