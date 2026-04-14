"""Tech Advisory Council — Telegram bot entry point.

Seven expert AI agents debate your product ideas, technical decisions, and dev questions.
"""

import asyncio
import json
import logging
import os
import signal
from dataclasses import asdict

import aiohttp.web
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import agents as agents_module
import memory
import profile as profile_module
import search
from categorizer import categorize_message
from debate import run_debate, generate_clarification_questions, map_answers_to_context
from utils import escape_md2, safe_send

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------

anthropic_client = AsyncAnthropic()

# Active debate sessions: chat_id → session state
active_sessions: dict[int, dict] = {}


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — introduce the Tech Council."""
    text = (
        "🏛️ *ברוכים הבאים למועצה הטכנולוגית*\n\n"
        "אני צוות ה\\-CTO האישי שלך — 7 יועצים מומחים שיתווכחו "
        "על הרעיונות, ההחלטות הטכניות, ושאלות הפיתוח שלך\\.\n\n"
        "🏗️ The Architect — ארכיטקטורת מערכות\n"
        "📋 The Speccer — הגדרת מוצר\n"
        "⚡ The Builder — פיתוח פרגמטי\n"
        "🛡️ The Guardian — אבטחה\n"
        "💰 The Monetizer — כדאיות עסקית\n"
        "🎨 The Simplifier — חווית משתמש\n"
        "🔮 The Futurist — חשיבה קדימה\n\n"
        "פשוט שלח את השאלה שלך ואני אפעיל את המועצה\\.\n\n"
        "*פקודות:*\n"
        "/new — סשן חדש\n"
        "/history — 5 דיונים אחרונים\n"
        "/decisions — החלטות שקיבלת\n"
        "/profile — הפרופיל הטכנולוגי שלך\n"
        "/decided \\<טקסט\\> — תעד מה החלטת\n"
        "/reflect — תובנות מההיסטוריה שלך"
    )
    await safe_send(context.bot, update.effective_chat.id, text)


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/new — clear active session, ready for new question."""
    chat_id = update.effective_chat.id
    active_sessions.pop(chat_id, None)
    await safe_send(context.bot, chat_id, escape_md2("✅ מוכן לשאלה חדשה. שלח את הנושא."))


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/history — show last 5 sessions."""
    chat_id = update.effective_chat.id
    user_id = str(chat_id)

    try:
        sessions = await memory.get_history(user_id, limit=5)
    except Exception as e:
        logger.error(f"Failed to fetch history: {e}")
        await safe_send(context.bot, chat_id, escape_md2("❌ שגיאה בטעינת ההיסטוריה."))
        return

    if not sessions:
        await safe_send(context.bot, chat_id, escape_md2("אין היסטוריה עדיין. שלח שאלה ראשונה!"))
        return

    lines = ["📜 *היסטוריית דיונים*\n"]
    for i, s in enumerate(sessions, 1):
        topic = (s.get("topic") or "")[:80]
        cat = s.get("category", "?")
        comp = s.get("complexity", "?")
        voted = s.get("voted_agent", "—")
        date = (s.get("created_at") or "")[:10]
        lines.append(
            f"{i}\\. \\[{escape_md2(date)}\\] *{escape_md2(cat)}* \\({escape_md2(comp)}\\)\n"
            f"   {escape_md2(topic)}\n"
            f"   הצבעה: {escape_md2(voted)}\n"
        )

    await safe_send(context.bot, chat_id, "\n".join(lines))


async def decisions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/decisions — list logged decisions."""
    chat_id = update.effective_chat.id
    user_id = str(chat_id)

    try:
        decisions = await memory.get_decisions(user_id)
    except Exception as e:
        logger.error(f"Failed to fetch decisions: {e}")
        await safe_send(context.bot, chat_id, escape_md2("❌ שגיאה בטעינת ההחלטות."))
        return

    if not decisions:
        await safe_send(context.bot, chat_id, escape_md2("אין החלטות מתועדות. השתמש ב- /decided <טקסט> אחרי דיון."))
        return

    lines = ["📝 *ההחלטות שלך*\n"]
    for i, d in enumerate(decisions, 1):
        text = (d.get("decision_text") or "")[:100]
        tools = d.get("tools_mentioned") or []
        date = (d.get("created_at") or "")[:10]
        tools_str = ", ".join(tools) if tools else "—"
        lines.append(
            f"{i}\\. \\[{escape_md2(date)}\\] {escape_md2(text)}\n"
            f"   כלים: {escape_md2(tools_str)}\n"
        )

    await safe_send(context.bot, chat_id, "\n".join(lines))


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/profile — show tech profile with insights."""
    chat_id = update.effective_chat.id
    user_id = str(chat_id)

    try:
        profile = await memory.get_profile(user_id)
    except Exception as e:
        logger.error(f"Failed to fetch profile: {e}")
        await safe_send(context.bot, chat_id, escape_md2("❌ שגיאה בטעינת הפרופיל."))
        return

    text = profile_module.generate_profile_text(profile)
    await safe_send(context.bot, chat_id, escape_md2(text))


async def decided_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/decided <text> — log what you decided after a session."""
    chat_id = update.effective_chat.id
    user_id = str(chat_id)

    # Extract decision text after /decided
    message_text = update.message.text or ""
    parts = message_text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await safe_send(context.bot, chat_id, escape_md2("שימוש: /decided <מה שהחלטת לעשות>"))
        return

    decision_text = parts[1].strip()

    # Get session ID if available
    session = active_sessions.get(chat_id)
    session_id = session.get("session_id") if session else None

    # Detect tools in decision text
    classification = categorize_message(decision_text)
    tools = classification["tools_mentioned"]

    try:
        await memory.save_decision(user_id, session_id, decision_text, tools)
        await safe_send(
            context.bot, chat_id,
            escape_md2(f"✅ ההחלטה תועדה: {decision_text}")
        )
    except Exception as e:
        logger.error(f"Failed to save decision: {e}")
        await safe_send(context.bot, chat_id, escape_md2("❌ שגיאה בשמירת ההחלטה."))


async def reflect_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reflect — revisit patterns and insights."""
    chat_id = update.effective_chat.id
    user_id = str(chat_id)

    try:
        profile = await memory.get_profile(user_id)
    except Exception as e:
        logger.error(f"Failed to fetch profile: {e}")
        await safe_send(context.bot, chat_id, escape_md2("❌ שגיאה."))
        return

    if not profile:
        await safe_send(context.bot, chat_id, escape_md2("אין מספיק נתונים עדיין. המשך להשתמש במועצה!"))
        return

    insights = profile_module.generate_pattern_insights(profile)
    if insights:
        await safe_send(context.bot, chat_id, escape_md2(f"💡 תובנות מההיסטוריה שלך:\n\n{insights}"))
    else:
        await safe_send(context.bot, chat_id, escape_md2("אין תובנות חדשות עדיין. צריך עוד כמה סשנים."))


# ---------------------------------------------------------------------------
# Skip phrases — user wants to bypass clarification
# ---------------------------------------------------------------------------

SKIP_PHRASES = {"skip", "דלג", "התחל", "start", "go", "בלי שאלות", "תמשיך"}


def _is_skip(text: str) -> bool:
    return text.strip().lower() in SKIP_PHRASES


# ---------------------------------------------------------------------------
# Core: run debate and send vote keyboard
# ---------------------------------------------------------------------------

async def _run_debate_and_vote(
    chat_id: int,
    user_id: str,
    question: str,
    category: str,
    complexity: str,
    tools: list[str],
    search_results: list,
    selected_agents: list,
    clarification_questions: list | None,
    clarification_answers: str | None,
    clarification_context: str | None,
    bot,
) -> None:
    """Execute debate, save session, send vote keyboard."""

    async def send_fn(msg: str):
        await safe_send(bot, chat_id, msg)

    await bot.send_chat_action(chat_id=chat_id, action="typing")

    result = await run_debate(
        client=anthropic_client,
        question=question,
        category=category,
        complexity=complexity,
        search_results=search_results,
        agents=selected_agents,
        send_fn=send_fn,
        clarification_context=clarification_context,
    )

    # Save session
    try:
        session_id = await memory.save_session(
            user_id=user_id,
            topic=question[:500],
            category=category,
            complexity=complexity,
            debate_rounds={"rounds": result.rounds, "summary": result.summary},
            clarification_questions=clarification_questions,
            clarification_answers=clarification_answers,
        )
    except Exception as e:
        logger.error(f"Failed to save session: {e}")
        session_id = None

    # Update state to VOTING
    active_sessions[chat_id] = {
        "status": "VOTING",
        "session_id": session_id,
        "category": category,
        "tools": tools,
        "selected_agents": selected_agents,
        "result": {
            "question": result.question,
            "category": result.category,
            "complexity": result.complexity,
            "participating_agents": result.participating_agents,
        },
    }

    # Vote keyboard
    keyboard = [
        [InlineKeyboardButton(f"{a.emoji} {a.name}", callback_data=f"vote_{a.id}")]
        for a in selected_agents
    ]
    keyboard.append([InlineKeyboardButton("🚫 בלי הצבעה", callback_data="vote_none")])
    await bot.send_message(
        chat_id=chat_id,
        text="🗳️ איזה יועץ הכי דיבר אליך?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------------------------------------------------------------------
# Message handler — triggers clarification OR debate
# ---------------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any non-command text message."""
    chat_id = update.effective_chat.id
    user_id = str(chat_id)
    text = (update.message.text or "").strip()

    if not text:
        return

    existing = active_sessions.get(chat_id)

    # --- Route to clarification reply handler ---
    if existing and existing.get("status") == "CLARIFYING":
        await _handle_clarification_reply(chat_id, user_id, text, context)
        return

    # --- Block during active debate ---
    if existing and existing.get("status") == "DEBATING":
        await safe_send(context.bot, chat_id, escape_md2("⏳ דיון כבר רץ. חכה שיסתיים או שלח /new."))
        return

    # --- New question flow ---
    active_sessions[chat_id] = {"status": "DEBATING"}

    try:
        # 1. Categorize
        classification = categorize_message(text)
        category = classification["category"]
        complexity = classification["complexity"]
        tools = classification["tools_mentioned"]

        # 2. Search
        search_results = await search.search_context(text)

        # 3. Select agents
        selected_agents = agents_module.select_agents(category, complexity)

        # 4. Phase 0 — Clarification check
        await safe_send(context.bot, chat_id, escape_md2("🔍 המועצה בוחנת את השאלה לפני שמתחילים..."))
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        from search import format_search_context
        search_str = format_search_context(search_results)
        questions = await generate_clarification_questions(anthropic_client, text, search_str)

        if not questions:
            # Enough detail — go straight to debate
            await safe_send(context.bot, chat_id, escape_md2("✅ פירטת מספיק — מכנס את המועצה עכשיו..."))
            await _run_debate_and_vote(
                chat_id=chat_id, user_id=user_id, question=text,
                category=category, complexity=complexity, tools=tools,
                search_results=search_results, selected_agents=selected_agents,
                clarification_questions=None, clarification_answers=None,
                clarification_context=None, bot=context.bot,
            )
        else:
            # Store state and ask questions
            active_sessions[chat_id] = {
                "status": "CLARIFYING",
                "question": text,
                "category": category,
                "complexity": complexity,
                "tools": tools,
                "search_results": search_results,
                "selected_agents": selected_agents,
                "clarification_questions": questions,
            }
            await _send_clarification_questions(chat_id, questions, context.bot)

    except Exception as e:
        logger.error(f"Message handling failed: {e}", exc_info=True)
        active_sessions.pop(chat_id, None)
        await safe_send(context.bot, chat_id, escape_md2("❌ שגיאה. נסה שוב."))


async def _send_clarification_questions(chat_id: int, questions: list[dict], bot) -> None:
    """Format and send clarification questions to the user."""
    lines = ["לפני שהמועצה מתחילה, יש כמה שאלות:\n"]
    for q in questions:
        asked_by = q.get("asked_by", "יועץ")
        question = q.get("question", "")
        why = q.get("why_it_matters", "")
        lines.append(f"{asked_by} שואל:\n{question}")
        if why:
            lines.append(f"_({why})_\n")
    lines.append("\nענה על הכל בהודעה אחת. או שלח 'דלג' כדי להתחיל בלי הבהרות.")
    await safe_send(bot, chat_id, escape_md2("\n".join(lines)))


async def _handle_clarification_reply(
    chat_id: int, user_id: str, text: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Process user's clarification answers, then launch debate."""
    session = active_sessions.get(chat_id)
    if not session:
        return

    original_question = session["question"]
    category = session["category"]
    complexity = session["complexity"]
    tools = session["tools"]
    search_results = session["search_results"]
    selected_agents = session["selected_agents"]
    questions = session["clarification_questions"]

    # Mark as debating to block re-entry
    active_sessions[chat_id] = {"status": "DEBATING"}

    try:
        if _is_skip(text):
            # User skipped — no clarification context
            await safe_send(context.bot, chat_id, escape_md2("⏭️ דולג — מכנס את המועצה..."))
            clarification_context = None
            clarification_answers = None
        else:
            # Map answers → context summary
            await safe_send(context.bot, chat_id, escape_md2("✅ תודה — מסכם את הפרטים ומכנס את המועצה..."))
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            clarification_context = await map_answers_to_context(
                anthropic_client, original_question, questions, text
            )
            clarification_answers = text

        await _run_debate_and_vote(
            chat_id=chat_id, user_id=user_id, question=original_question,
            category=category, complexity=complexity, tools=tools,
            search_results=search_results, selected_agents=selected_agents,
            clarification_questions=questions,
            clarification_answers=clarification_answers,
            clarification_context=clarification_context,
            bot=context.bot,
        )

    except Exception as e:
        logger.error(f"Clarification reply failed: {e}", exc_info=True)
        active_sessions.pop(chat_id, None)
        await safe_send(context.bot, chat_id, escape_md2("❌ שגיאה. נסה שוב."))


# ---------------------------------------------------------------------------
# Vote callback
# ---------------------------------------------------------------------------

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline keyboard vote."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = str(chat_id)
    voted = query.data.replace("vote_", "")

    session = active_sessions.get(chat_id)
    if not session or session.get("status") != "VOTING":
        return

    session_id = session.get("session_id")
    category = session.get("category", "other")
    tools = session.get("tools", [])

    # Record vote in session
    if session_id and voted != "none":
        try:
            await memory.update_session_vote(session_id, voted)
        except Exception as e:
            logger.error(f"Failed to update session vote: {e}")

    # Update profile
    try:
        await memory.update_profile(
            user_id=user_id,
            category=category,
            voted_agent=voted if voted != "none" else None,
            tools=tools,
        )
    except Exception as e:
        logger.error(f"Failed to update profile: {e}")

    # Generate response
    if voted == "none":
        response_text = "👍 מובן, בלי הצבעה הפעם."
    else:
        agent = agents_module.get_agent(voted)
        agent_name = agent.name if agent else voted
        response_text = f"✅ הצבעה נרשמה: {agent_name}"

    # Check for insights
    try:
        profile = await memory.get_profile(user_id)
        insights = profile_module.generate_pattern_insights(profile)
        if insights:
            response_text += f"\n\n💡 {insights}"
    except Exception:
        pass

    response_text += "\n\nהחלטת מה לעשות? השתמש ב- /decided <מה שהחלטת>"

    await safe_send(context.bot, chat_id, escape_md2(response_text))
    session["status"] = "DECIDED"


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler."""
    logger.error(f"Exception while handling update: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await safe_send(
                context.bot,
                update.effective_chat.id,
                escape_md2("❌ שגיאה בלתי צפויה. נסה שוב."),
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Health check server
# ---------------------------------------------------------------------------

async def health_check(request: aiohttp.web.Request) -> aiohttp.web.Response:
    return aiohttp.web.json_response({"status": "ok", "bot": "tech-council"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    """Start bot + health check on same event loop."""
    token = os.environ["TECH_BOT_TOKEN"]
    port = int(os.environ.get("PORT", "8080"))

    # Build Telegram application
    app = Application.builder().token(token).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("decisions", decisions_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("decided", decided_command))
    app.add_handler(CommandHandler("reflect", reflect_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_vote, pattern=r"^vote_"))
    app.add_error_handler(error_handler)

    # Initialize and start telegram
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("Telegram bot started")

    # Start health check server
    web_app = aiohttp.web.Application()
    web_app.router.add_get("/health", health_check)
    web_app.router.add_get("/", health_check)
    runner = aiohttp.web.AppRunner(web_app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"Health check server started on port {port}")

    # Keep running until interrupted
    stop_event = asyncio.Event()

    def _signal_handler():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
