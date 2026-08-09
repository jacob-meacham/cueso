"""Live TMDB smoke test: oracle timing + end-to-end filtered search.

Run from backend/ (needs config.yml with brave + tmdb keys):
    uv run python scripts/tmdb_smoke.py
"""

import asyncio
import time

import httpx

from app.core.brave_search import BraveSearchClient
from app.core.config import settings
from app.core.search_and_play import search_content
from app.core.tmdb import TMDBClient


async def main() -> None:
    assert settings.tmdb.api_key is not None, "tmdb.api_key missing from config.yml"
    assert settings.brave.api_key is not None, "brave.api_key missing from config.yml"

    async with httpx.AsyncClient() as http_client:
        tmdb = TMDBClient(
            api_key=settings.tmdb.api_key.get_secret_value(),
            region=settings.tmdb.region,
            http_client=http_client,
        )

        for title in ["Bye Bye Birdie", "The Bear", "Moana 2"]:
            t0 = time.perf_counter()
            services = await tmdb.get_streamable_services(title)
            print(f"oracle {title!r}: {(time.perf_counter() - t0) * 1000:.0f}ms -> {services}")

        brave = BraveSearchClient(api_key=settings.brave.api_key.get_secret_value(), http_client=http_client)
        t0 = time.perf_counter()
        result = await search_content("Bye Bye Birdie", brave_client=brave, http_client=http_client, tmdb_client=tmdb)
        print(f"\nsearch_content('Bye Bye Birdie'): {(time.perf_counter() - t0) * 1000:.0f}ms")
        print(f"  message: {result.message}")
        for m in result.matches:
            print(f"  {m.service_name}: {m.content_id}")
        assert all(m.service_name != "netflix" for m in result.matches), "Netflix placeholder not filtered!"


asyncio.run(main())
