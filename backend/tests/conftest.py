"""Shared test fixtures for the backend test suite."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.brave_search import BraveSearchClient


@pytest.fixture
def mock_http_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def mock_brave_client() -> AsyncMock:
    return AsyncMock(spec=BraveSearchClient)
