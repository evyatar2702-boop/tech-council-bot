"""Telegram formatting utilities — MarkdownV2 escaping, message splitting, safe sending."""

import logging
from telegram import Bot
from telegram.error import BadRequest

logger = logging.getLogger(__name__)

MARKDOWN_V2_SPECIAL = r"\_*[]()~`>#+-=|{}.!"


def escape_md2(text: str) -> str:
    """Escape all MarkdownV2 special characters."""
    result = []
    for char in text:
        if char in MARKDOWN_V2_SPECIAL:
            result.append(f"\\{char}")
        else:
            result.append(char)
    return "".join(result)


def split_message(text: str, max_length: int = 4000) -> list[str]:
    """Split long text into chunks that fit Telegram's 4096 char limit.

    Split strategy: paragraphs → lines → sentences → hard cut.
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try splitting on double newline (paragraph)
        cut_point = remaining.rfind("\n\n", 0, max_length)
        if cut_point > max_length // 2:
            chunks.append(remaining[:cut_point])
            remaining = remaining[cut_point + 2:]
            continue

        # Try splitting on single newline
        cut_point = remaining.rfind("\n", 0, max_length)
        if cut_point > max_length // 2:
            chunks.append(remaining[:cut_point])
            remaining = remaining[cut_point + 1:]
            continue

        # Try splitting on sentence
        cut_point = remaining.rfind(". ", 0, max_length)
        if cut_point > max_length // 2:
            chunks.append(remaining[: cut_point + 1])
            remaining = remaining[cut_point + 2:]
            continue

        # Hard cut
        chunks.append(remaining[:max_length])
        remaining = remaining[max_length:]

    return chunks


def format_agent_message(emoji: str, name: str, content: str) -> str:
    """Format an agent's response for Telegram MarkdownV2."""
    escaped_name = escape_md2(name)
    escaped_content = escape_md2(content)
    return f"{emoji} *{escaped_name}*\n\n{escaped_content}"


def format_summary(content: str) -> str:
    """Format the CTO summary message."""
    escaped = escape_md2(content)
    return f"🎯 *סיכום ה\\-CTO*\n\n{escaped}"


async def safe_send(bot: Bot, chat_id: int, text: str, parse_mode: str = "MarkdownV2") -> None:
    """Send message with MarkdownV2, fallback to plain text on error. Auto-splits long messages."""
    chunks = split_message(text)
    for chunk in chunks:
        if not chunk.strip():
            continue
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode)
        except BadRequest:
            # MarkdownV2 parsing failed — strip escape chars and send plain
            plain = chunk.replace("\\", "")
            try:
                await bot.send_message(chat_id=chat_id, text=plain)
            except Exception as e:
                logger.error(f"Failed to send message even as plain text: {e}")
