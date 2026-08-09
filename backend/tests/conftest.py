"""Shared test fixtures for the backend test suite."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.brave_search import BraveSearchClient
from app.core.emby import EmbyClient


@pytest.fixture
def mock_http_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def mock_brave_client() -> AsyncMock:
    return AsyncMock(spec=BraveSearchClient)


@pytest.fixture
def mock_emby_client() -> AsyncMock:
    client = AsyncMock(spec=EmbyClient)
    client.server_url = "http://emby.local:8096"
    return client


@pytest.fixture
def mock_tmdb_client() -> AsyncMock:
    from app.core.tmdb import TMDBClient

    return AsyncMock(spec=TMDBClient)
