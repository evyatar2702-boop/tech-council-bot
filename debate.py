"""Core debate orchestration — parallel agent calls, rounds, cross-examination, CTO summary."""

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Awaitable

from anthropic import AsyncAnthropic

from agents import Agent
from search import format_search_context
from utils import format_agent_message, format_summary

logger = logging.getLogger(__name__)

# Concurrency limiter — avoid rate limits on Anthropic API
_semaphore = asyncio.Semaphore(5)

MODEL = "claude-sonnet-4-6"

# Token limits per complexity and round
TOKEN_LIMITS = {
    "quick":  {"round1": 300, "round2": 0, "summary": 400},
    "medium": {"round1": 300, "round2": 250, "summary": 500},
    "deep":   {"round1": 500, "round2": 400, "summary": 800},
}

# Type alias for the Telegram message callback
SendFn = Callable[[str], Awaitable[None]]


@dataclass
class AgentResponse:
    agent_id: str
    agent_name: str
    agent_emoji: str
    round_num: int
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class DebateResult:
    question: str
    category: str
    complexity: str
    search_context: list[dict]
    rounds: list[list[dict]]  # serializable version of AgentResponse
    summary: str
    participating_agents: list[str]


# ---------------------------------------------------------------------------
# Single agent API call
# ---------------------------------------------------------------------------

async def _call_agent(
    client: AsyncAnthropic,
    agent: Agent,
    question: str,
    search_context_str: str,
    round_num: int,
    prior_transcript: str | None,
    max_tokens: int,
) -> AgentResponse:
    """Call a single agent via Anthropic API. Isolated failure — never raises."""
    system = agent.system_prompt
    if search_context_str:
        system += f"\n\n{search_context_str}"

    # Build user message
    if round_num == 1:
        user_content = (
            f"הנושא לדיון:\n{question}\n\n"
            "תן את עמדתך המקצועית. תהיה קונקרטי וממוקד."
        )
    else:
        user_content = (
            f"הנושא המקורי:\n{question}\n\n"
            f"--- עמדות סבב 1 ---\n{prior_transcript}\n"
            "--- סוף סבב 1 ---\n\n"
            "סבב 2 — בדוק מה אמרו שאר היועצים. "
            "התייחס ליועץ ספציפי בשמו — תגבה, תאתגר, או תשכלל את הנקודה שלו. "
            "The Builder ו-The Speccer בדרך כלל חולקים — אם אתה אחד מהם, אתגר את השני."
        )

    async with _semaphore:
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
            content = response.content[0].text
        except Exception as e:
            logger.error(f"Agent {agent.id} failed in round {round_num}: {e}")
            content = f"({agent.name} לא הצליח להגיב — שגיאה טכנית)"

    return AgentResponse(
        agent_id=agent.id,
        agent_name=agent.name,
        agent_emoji=agent.emoji,
        round_num=round_num,
        content=content,
    )


# ---------------------------------------------------------------------------
# Build round transcript
# ---------------------------------------------------------------------------

def _build_transcript(responses: list[AgentResponse]) -> str:
    """Build a readable transcript of agent responses for cross-examination context."""
    lines = []
    for r in responses:
        lines.append(f"{r.agent_emoji} {r.agent_name}:\n{r.content}\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CTO Summary
# ---------------------------------------------------------------------------

async def _generate_summary(
    client: AsyncAnthropic,
    question: str,
    all_responses: list[AgentResponse],
    search_context_str: str,
    max_tokens: int,
) -> str:
    """Generate the CTO synthesis summary."""
    transcript = _build_transcript(all_responses)

    system = (
        "אתה Lead CTO — מסנתז את כל הדעות של צוות היועצים שלך לתוכנית פעולה ברורה. "
        "אתה מכיר את Base44, Supabase, GoHighLevel, Anthropic API. "
        "אתה יודע שהמשתמש הוא מייסד יחיד עם זמן מוגבל."
    )
    if search_context_str:
        system += f"\n\n{search_context_str}"

    user_content = (
        f"השאלה המקורית:\n{question}\n\n"
        f"--- דיון היועצים ---\n{transcript}\n"
        "--- סוף הדיון ---\n\n"
        "סכם את הדיון:\n"
        "1. מה ההחלטה הטכנית המרכזית שצריך לקבל\n"
        "2. על מה היועצים מסכימים\n"
        "3. הסיכון האחד שאף אחד לא צריך להתעלם ממנו\n"
        "4. צעד הבא מומלץ (קונקרטי, משפט אחד)\n\n"
        'סיים עם: "אם הייתי במקומך, הייתי [המלצה ספציפית]."'
    )

    async with _semaphore:
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"CTO Summary failed: {e}")
            return "לא הצלחתי לסנתז את הדיון. עיין בעמדות היועצים למעלה."


# ---------------------------------------------------------------------------
# Main debate orchestration
# ---------------------------------------------------------------------------

async def run_debate(
    client: AsyncAnthropic,
    question: str,
    category: str,
    complexity: str,
    search_results: list[dict],
    agents: list[Agent],
    send_fn: SendFn,
) -> DebateResult:
    """Run the full debate flow. Sends messages progressively via send_fn.

    Args:
        client: AsyncAnthropic client
        question: User's question/idea
        category: Auto-detected category
        complexity: 'quick', 'medium', or 'deep'
        search_results: Tavily search results
        agents: Selected agents for this debate
        send_fn: Async callback to send a Telegram message
    """
    limits = TOKEN_LIMITS[complexity]
    search_str = format_search_context(search_results)
    all_responses: list[AgentResponse] = []

    # --- Header ---
    agent_count = len(agents)
    if complexity == "quick":
        header = f"⚡ דיון מהיר — {agent_count} יועצים • סבב אחד • קטגוריה: {category}"
    elif complexity == "medium":
        header = f"💬 דיון מלא — {agent_count} יועצים • 2 סבבים • קטגוריה: {category}"
    else:
        header = f"🔬 דיון מעמיק — {agent_count} יועצים • 2 סבבים + סינתזה מורחבת • קטגוריה: {category}"

    if search_results:
        header += f"\n🔍 נמצאו {len(search_results)} מקורות רלוונטיים מהרשת"

    await send_fn(header)

    # --- Round 1 ---
    await send_fn("━━━ סבב 1: עמדות ━━━")

    round1_tasks = [
        asyncio.create_task(
            _call_agent(client, agent, question, search_str, 1, None, limits["round1"])
        )
        for agent in agents
    ]

    round1_responses: list[AgentResponse] = []
    for coro in asyncio.as_completed(round1_tasks):
        response = await coro
        round1_responses.append(response)
        all_responses.append(response)
        msg = format_agent_message(response.agent_emoji, response.agent_name, response.content)
        await send_fn(msg)

    # --- Round 2 (medium and deep only) ---
    if complexity in ("medium", "deep") and limits["round2"] > 0:
        await send_fn("━━━ סבב 2: תגובות הדדיות ━━━")

        transcript = _build_transcript(round1_responses)
        round2_tasks = [
            asyncio.create_task(
                _call_agent(client, agent, question, search_str, 2, transcript, limits["round2"])
            )
            for agent in agents
        ]

        round2_responses: list[AgentResponse] = []
        for coro in asyncio.as_completed(round2_tasks):
            response = await coro
            round2_responses.append(response)
            all_responses.append(response)
            msg = format_agent_message(response.agent_emoji, response.agent_name, response.content)
            await send_fn(msg)

    # --- CTO Summary ---
    await send_fn("━━━━━━━━━━━━━━━━━━━━")
    summary_text = await _generate_summary(
        client, question, all_responses, search_str, limits["summary"]
    )
    await send_fn(format_summary(summary_text))

    # --- Build result ---
    rounds_data = []
    # Group responses by round
    r1 = [asdict(r) for r in all_responses if r.round_num == 1]
    rounds_data.append(r1)
    r2 = [asdict(r) for r in all_responses if r.round_num == 2]
    if r2:
        rounds_data.append(r2)

    return DebateResult(
        question=question,
        category=category,
        complexity=complexity,
        search_context=search_results,
        rounds=rounds_data,
        summary=summary_text,
        participating_agents=[a.id for a in agents],
    )
