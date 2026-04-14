"""Supabase persistence layer for tech sessions, decisions, and profiles.

SQL to create tables (run in Supabase SQL editor):

CREATE TABLE tech_sessions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    user_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    category TEXT,
    complexity TEXT,
    debate_rounds JSONB,
    voted_agent TEXT,
    implementation_started BOOLEAN DEFAULT FALSE,
    outcome_note TEXT,
    clarification_questions JSONB,
    clarification_answers TEXT
);

-- If table already exists, run these instead:
-- ALTER TABLE tech_sessions ADD COLUMN IF NOT EXISTS clarification_questions JSONB;
-- ALTER TABLE tech_sessions ADD COLUMN IF NOT EXISTS clarification_answers TEXT;

CREATE TABLE tech_decisions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    session_id UUID REFERENCES tech_sessions(id),
    decision_text TEXT NOT NULL,
    tools_mentioned TEXT[],
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE tech_profile (
    user_id TEXT PRIMARY KEY,
    total_sessions INT DEFAULT 0,
    vote_history JSONB DEFAULT '{}',
    recurring_tools TEXT[] DEFAULT '{}',
    weak_spots JSONB DEFAULT '{}',
    last_seen TIMESTAMPTZ DEFAULT NOW()
);
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from supabase import create_client, Client

logger = logging.getLogger(__name__)

_supabase: Client | None = None


def _get_client() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )
    return _supabase


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

async def save_session(
    user_id: str,
    topic: str,
    category: str,
    complexity: str,
    debate_rounds: dict,
    clarification_questions: list | None = None,
    clarification_answers: str | None = None,
) -> str:
    """Insert a new tech_session. Returns the session UUID."""
    data = {
        "user_id": user_id,
        "topic": topic,
        "category": category,
        "complexity": complexity,
        "debate_rounds": debate_rounds,
    }
    if clarification_questions:
        data["clarification_questions"] = clarification_questions
    if clarification_answers:
        data["clarification_answers"] = clarification_answers

    def _insert():
        return _get_client().table("tech_sessions").insert(data).execute()

    result = await asyncio.to_thread(_insert)
    return result.data[0]["id"]


async def update_session_vote(session_id: str, voted_agent: str) -> None:
    """Record which agent the user voted for."""
    def _update():
        return (
            _get_client()
            .table("tech_sessions")
            .update({"voted_agent": voted_agent})
            .eq("id", session_id)
            .execute()
        )
    await asyncio.to_thread(_update)


async def get_history(user_id: str, limit: int = 5) -> list[dict]:
    """Get recent sessions for a user."""
    def _query():
        return (
            _get_client()
            .table("tech_sessions")
            .select("id, created_at, topic, category, complexity, voted_agent")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    result = await asyncio.to_thread(_query)
    return result.data


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

async def save_decision(
    user_id: str,
    session_id: str | None,
    decision_text: str,
    tools_mentioned: list[str],
) -> None:
    """Log a user decision."""
    data = {
        "decision_text": decision_text,
        "tools_mentioned": tools_mentioned,
    }
    if session_id:
        data["session_id"] = session_id

    def _insert():
        return _get_client().table("tech_decisions").insert(data).execute()

    await asyncio.to_thread(_insert)


async def get_decisions(user_id: str) -> list[dict]:
    """Get all decisions for a user (via their sessions)."""
    def _query():
        return (
            _get_client()
            .table("tech_decisions")
            .select("id, decision_text, tools_mentioned, created_at, session_id")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
    result = await asyncio.to_thread(_query)
    return result.data


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

async def get_profile(user_id: str) -> dict | None:
    """Get or create a user's tech profile."""
    def _query():
        return (
            _get_client()
            .table("tech_profile")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )

    result = await asyncio.to_thread(_query)
    if result.data:
        return result.data[0]
    return None


async def update_profile(
    user_id: str,
    category: str,
    voted_agent: str | None,
    tools: list[str],
) -> None:
    """Upsert the user's tech profile with new session data."""
    profile = await get_profile(user_id)

    if profile is None:
        # Create new profile
        profile = {
            "user_id": user_id,
            "total_sessions": 0,
            "vote_history": {},
            "recurring_tools": [],
            "weak_spots": {},
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }

    # Increment session count
    profile["total_sessions"] = profile.get("total_sessions", 0) + 1

    # Update vote history
    vote_history = profile.get("vote_history") or {}
    if voted_agent and voted_agent != "none":
        vote_history[voted_agent] = vote_history.get(voted_agent, 0) + 1
    profile["vote_history"] = vote_history

    # Update category history (weak_spots)
    weak_spots = profile.get("weak_spots") or {}
    weak_spots[category] = weak_spots.get(category, 0) + 1
    profile["weak_spots"] = weak_spots

    # Merge tools
    existing_tools = set(profile.get("recurring_tools") or [])
    existing_tools.update(tools)
    profile["recurring_tools"] = list(existing_tools)

    # Update timestamp
    profile["last_seen"] = datetime.now(timezone.utc).isoformat()

    def _upsert():
        return (
            _get_client()
            .table("tech_profile")
            .upsert(profile)
            .execute()
        )

    await asyncio.to_thread(_upsert)
