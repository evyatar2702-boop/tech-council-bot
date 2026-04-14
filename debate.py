"""Core debate orchestration — parallel agent calls, rounds, cross-examination, CTO summary."""

import asyncio
import json
import logging
import re
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
# Clarification phase
# ---------------------------------------------------------------------------

def _parse_json_array(text: str) -> list:
    """Robustly parse a JSON array from LLM output — strips markdown code fences."""
    # Strip ```json ... ``` or ``` ... ``` wrappers
    text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
    # Find first [ ... ] block
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    return json.loads(text)


async def generate_clarification_questions(
    client: AsyncAnthropic,
    question: str,
    search_context_str: str,
) -> list[dict]:
    """Ask intake panel what critical information is missing.

    Returns list of {question, asked_by, why_it_matters} dicts, or [] if enough detail.
    """
    system = (
        "You are a technical intake panel representing 7 experts: System Architect, "
        "Product Spec Expert, Implementation Expert, Security Expert, Business Viability Expert, "
        "UX Expert, and Scalability Expert.\n\n"
        "Your job: read the user's topic and identify every piece of information "
        "that is genuinely missing and would materially change the advice each expert gives. "
        "Do not ask for nice-to-have info — only critical gaps.\n\n"
        "Use your judgment on how many questions to ask:\n"
        "- If the user gave enough detail: return an empty array, no questions needed\n"
        "- If one question covers the main gap: ask one\n"
        "- If the topic is complex and vague: ask as many as truly needed\n\n"
        "Each question should be attributed to the expert who cares about it most.\n\n"
        "Return ONLY a valid JSON array (can be empty):\n"
        '[{"question": "the question text", "asked_by": "expert name and emoji", '
        '"why_it_matters": "one sentence explaining what changes based on answer"}]\n'
        "No preamble. No markdown. Only the JSON array."
    )

    if search_context_str:
        system += f"\n\n{search_context_str}"

    async with _semaphore:
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=600,
                system=system,
                messages=[{"role": "user", "content": f"Topic: {question}"}],
            )
            raw = response.content[0].text.strip()
            questions = _parse_json_array(raw)
            # Validate structure
            return [
                q for q in questions
                if isinstance(q, dict) and "question" in q and "asked_by" in q
            ]
        except Exception as e:
            logger.error(f"Clarification questions failed: {e}")
            return []  # Fail open — skip clarification


async def map_answers_to_context(
    client: AsyncAnthropic,
    original_question: str,
    questions: list[dict],
    user_answer: str,
) -> str:
    """Synthesize Q&A into a clean context paragraph for agents."""
    qa_pairs = "\n".join(
        f"Q ({q['asked_by']}): {q['question']}\nA: {user_answer}"
        for q in questions
    )

    system = (
        "You are a technical context summarizer. "
        "Given a topic and Q&A clarification exchange, write a concise factual paragraph "
        "summarizing the key context that expert advisors need to know. "
        "No fluff. Just facts from the answers. "
        "Respond in the same language as the original topic."
    )

    async with _semaphore:
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=300,
                system=system,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Original topic: {original_question}\n\n"
                        f"Clarification Q&A:\n{qa_pairs}\n\n"
                        "Write a context summary paragraph:"
                    ),
                }],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"map_answers_to_context failed: {e}")
            # Fallback: just use the raw answer
            return f"פרטים שהמשתמש סיפק: {user_answer}"


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
    clarification_context: str | None = None,
) -> AgentResponse:
    """Call a single agent via Anthropic API. Isolated failure — never raises."""
    system = agent.system_prompt
    if search_context_str:
        system += f"\n\n{search_context_str}"
    if clarification_context:
        system += (
            f"\n\nהמשתמש סיפק פרטים נוספים לפני הדיון:\n{clarification_context}\n"
            "התחשב בכל זה בתשובתך."
        )

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
    clarification_context: str | None = None,
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
        clarification_context: Optional pre-debate clarification summary
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
            _call_agent(client, agent, question, search_str, 1, None, limits["round1"], clarification_context)
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
                _call_agent(client, agent, question, search_str, 2, transcript, limits["round2"], clarification_context)
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
