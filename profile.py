"""User profile analysis — pattern insights and profile text generation."""

from agents import AGENTS


def generate_profile_text(profile: dict) -> str:
    """Format a user's tech profile into a readable Telegram message."""
    if not profile:
        return "אין עדיין פרופיל. שלח שאלה ראשונה כדי להתחיל!"

    total = profile.get("total_sessions", 0)
    vote_history = profile.get("vote_history") or {}
    weak_spots = profile.get("weak_spots") or {}
    tools = profile.get("recurring_tools") or []

    lines = ["📊 הפרופיל הטכנולוגי שלך", ""]

    # Total sessions
    lines.append(f"סה\"כ סשנים: {total}")

    # Favorite advisor
    if vote_history:
        top_agent_id = max(vote_history, key=vote_history.get)
        top_count = vote_history[top_agent_id]
        agent = AGENTS.get(top_agent_id)
        if agent:
            lines.append(f"יועץ מועדף: {agent.emoji} {agent.name} ({top_count} הצבעות)")

    # Top categories (weak spots)
    if weak_spots:
        sorted_cats = sorted(weak_spots.items(), key=lambda x: x[1], reverse=True)[:3]
        cats_str = ", ".join(f"{cat} ({count})" for cat, count in sorted_cats)
        lines.append(f"נושאים נפוצים: {cats_str}")

    # Tools in stack
    if tools:
        lines.append(f"כלים בשימוש: {', '.join(tools)}")

    # Vote distribution
    if len(vote_history) > 1:
        lines.append("")
        lines.append("התפלגות הצבעות:")
        sorted_votes = sorted(vote_history.items(), key=lambda x: x[1], reverse=True)
        for agent_id, count in sorted_votes:
            agent = AGENTS.get(agent_id)
            if agent:
                bar = "█" * count
                lines.append(f"  {agent.emoji} {agent.name}: {bar} ({count})")

    # Pattern insights
    insights = generate_pattern_insights(profile)
    if insights:
        lines.append("")
        lines.append("💡 תובנות:")
        lines.append(insights)

    return "\n".join(lines)


def generate_pattern_insights(profile: dict) -> str | None:
    """Detect patterns in user behavior and return insight text.

    Returns None if not enough data or no patterns detected.
    """
    if not profile:
        return None

    total = profile.get("total_sessions", 0)
    if total < 3:
        return None

    insights = []
    vote_history = profile.get("vote_history") or {}
    weak_spots = profile.get("weak_spots") or {}
    tools = profile.get("recurring_tools") or []

    # --- Pattern: Always trusts same advisor ---
    if vote_history:
        top_agent_id = max(vote_history, key=vote_history.get)
        top_count = vote_history[top_agent_id]
        total_votes = sum(vote_history.values())

        if total_votes >= 5 and len(vote_history) == 1:
            agent = AGENTS.get(top_agent_id)
            name = agent.name if agent else top_agent_id
            insights.append(
                f"אתה תמיד מצביע ל-{name}. שווה לשים לב למה הנקודות של שאר היועצים לא מהדהדות."
            )
        elif top_count >= 4 and total_votes >= 6:
            agent = AGENTS.get(top_agent_id)
            name = agent.name if agent else top_agent_id
            pct = round(top_count / total_votes * 100)
            insights.append(
                f"אתה סומך על {name} ב-{pct}% מהמקרים. שים לב שיועצים אחרים העלו נקודות חשובות שלא ביצעת."
            )

    # --- Pattern: Weak spot (asking same category a lot) ---
    if weak_spots:
        top_cat = max(weak_spots, key=weak_spots.get)
        top_count = weak_spots[top_cat]
        if top_count >= 4:
            insights.append(
                f"שאלת על {top_cat} כבר {top_count} פעמים — ייתכן שזה תחום שכדאי להשקיע בו ידע יותר עמוק."
            )

    # --- Pattern: Base44 ceiling ---
    if "Base44" in tools and weak_spots.get("feature", 0) >= 4:
        insights.append(
            "שאלת על פיצ'רים ב-Base44 מספר פעמים — ייתכן שאתה מגיע לתקרת היכולות של הפלטפורמה לחלק מהצרכים שלך."
        )

    # --- Pattern: Security blind spot ---
    if weak_spots.get("security", 0) == 0 and total >= 5:
        insights.append(
            "לא שאלת שאלת אבטחה אחת ב-{} סשנים. שווה לחשוב על זה.".format(total)
        )

    # --- Pattern: Builder vs Speccer tension ---
    builder_votes = vote_history.get("builder", 0)
    speccer_votes = vote_history.get("speccer", 0)
    if builder_votes >= 3 and speccer_votes == 0:
        insights.append(
            "אתה תמיד הולך עם The Builder (שלח מהר) ואף פעם לא עם The Speccer (תגדיר קודם). "
            "זה עובד עד שזה לא — שים לב."
        )
    elif speccer_votes >= 3 and builder_votes == 0:
        insights.append(
            "אתה תמיד הולך עם The Speccer (תגדיר קודם) ואף פעם לא עם The Builder (שלח מהר). "
            "לפעמים שווה פשוט לבנות ולראות מה קורה."
        )

    return "\n".join(insights) if insights else None
