"""Tavily web search — always-on context for expert agents."""

import asyncio
import logging
import os
import re

from tavily import TavilyClient

logger = logging.getLogger(__name__)

_client: TavilyClient | None = None


def _get_client() -> TavilyClient:
    global _client
    if _client is None:
        _client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _client


# Words to strip from search queries (Hebrew + English filler)
FILLER_WORDS = {
    "i", "me", "my", "should", "would", "could", "can", "do", "does", "is", "are",
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "what", "how", "why", "when", "where", "who", "which",
    "אני", "שלי", "האם", "כדאי", "אפשר", "צריך", "את", "של", "על", "עם", "מה", "איך",
    "למה", "מתי", "איפה", "מי", "לי", "זה", "הזה", "או", "אבל", "גם",
}


def _build_query(text: str) -> str:
    """Extract a search query from user message — keep technical terms, drop filler."""
    # Remove punctuation except hyphens and dots (for tool names like Next.js)
    cleaned = re.sub(r"[^\w\s.\-]", " ", text)
    words = cleaned.split()
    # Keep non-filler words
    filtered = [w for w in words if w.lower() not in FILLER_WORDS]
    query = " ".join(filtered)[:200]
    return query if query.strip() else text[:200]


def _search_sync(query: str) -> list[dict]:
    """Synchronous Tavily search — called via asyncio.to_thread."""
    client = _get_client()
    response = client.search(query=query, max_results=3, search_depth="basic")
    results = []
    for r in response.get("results", []):
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:300],
        })
    return results


async def search_context(user_message: str) -> list[dict]:
    """Run Tavily search on user's message. Returns top 3 results.

    Never blocks the debate — returns empty list on failure.
    """
    query = _build_query(user_message)
    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_search_sync, query),
            timeout=8.0,
        )
        return results
    except Exception as e:
        logger.warning(f"Tavily search failed (continuing without): {e}")
        return []


def format_search_context(results: list[dict]) -> str:
    """Format search results into a context string for agent prompts."""
    if not results:
        return ""
    lines = ["Recent web context:"]
    for r in results:
        lines.append(f"- {r['title']}: {r['snippet']} ({r['url']})")
    return "\n".join(lines)
