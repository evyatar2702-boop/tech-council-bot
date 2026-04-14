"""Auto-detect category, complexity, and mentioned tools from user messages."""

import re

# Keyword lists per category — matched case-insensitively
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "architecture": [
        "architecture", "ארכיטקטורה", "system design", "עיצוב מערכת",
        "microservice", "monolith", "database", "דאטאבייס", "schema", "סכמה",
        "api design", "data flow", "data model", "מודל נתונים",
        "infrastructure", "תשתית", "backend", "בקאנד",
        "scale", "סקייל", "load", "queue", "cache", "קאש",
    ],
    "feature": [
        "base44", "בייס 44", "field", "שדה", "page", "עמוד", "button", "כפתור",
        "component", "קומפוננטה", "form", "טופס", "widget", "וידג'ט",
        "add feature", "פיצ'ר", "build", "לבנות", "create", "ליצור",
        "workflow", "וורקפלו", "automation", "אוטומציה",
    ],
    "business": [
        "price", "מחיר", "charge", "לגבות", "sell", "למכור", "client", "לקוח",
        "revenue", "הכנסה", "roi", "profit", "רווח", "subscription", "מנוי",
        "churn", "retention", "שימור", "onboarding", "אונבורדינג",
        "market", "שוק", "competitor", "מתחרה", "pricing", "תמחור",
        "business model", "מודל עסקי",
    ],
    "security": [
        "security", "אבטחה", "hack", "פריצה", "password", "סיסמה",
        "auth", "אימות", "token", "טוקן", "encrypt", "הצפנה",
        "vulnerability", "פגיעות", "gdpr", "privacy", "פרטיות",
        "api key", "leak", "דליפה", "permission", "הרשאה",
        "rls", "row level security",
    ],
    "ux": [
        "ux", "יו אקס", "ui", "יו איי", "design", "עיצוב",
        "user experience", "חווית משתמש", "usability", "שימושיות",
        "simple", "פשוט", "confusing", "מבלבל", "intuitive", "אינטואיטיבי",
        "mobile", "מובייל", "responsive", "רספונסיבי",
        "flow", "פלואו", "screen", "מסך",
    ],
    "integration": [
        "integrate", "אינטגרציה", "webhook", "ווב הוק", "api", "אייפיאיי",
        "connect", "לחבר", "sync", "סנכרון", "zapier", "n8n",
        "gohighlevel", "ghl", "ג'י אייצ' אל",
        "whatsapp", "ווטסאפ", "telegram", "טלגרם",
        "third party", "צד שלישי",
    ],
    "strategy": [
        "should i", "האם כדאי", "worth it", "שווה", "idea", "רעיון",
        "pivot", "פיבוט", "direction", "כיוון", "priority", "עדיפות",
        "roadmap", "רודמאפ", "focus", "פוקוס", "launch", "השקה",
        "mvp", "אמ וי פי", "validate", "לוודא", "experiment", "ניסוי",
    ],
}

# Known tools — case-insensitive matching
KNOWN_TOOLS: list[tuple[str, list[str]]] = [
    ("Base44", ["base44", "בייס 44", "base 44"]),
    ("Supabase", ["supabase", "סופאבייס"]),
    ("GoHighLevel", ["gohighlevel", "ghl", "go high level", "ג'י אייצ' אל"]),
    ("Claude Code", ["claude code", "קלוד קוד"]),
    ("Anthropic", ["anthropic", "אנתרופיק", "claude", "קלוד"]),
    ("Tavily", ["tavily", "טאבילי"]),
    ("Render", ["render", "רנדר"]),
    ("n8n", ["n8n"]),
    ("Zapier", ["zapier", "זאפייר"]),
    ("WhatsApp", ["whatsapp", "ווטסאפ"]),
    ("Telegram", ["telegram", "טלגרם"]),
    ("Next.js", ["next.js", "nextjs", "נקסט"]),
    ("Vercel", ["vercel", "ורסל"]),
    ("React", ["react", "ריאקט"]),
]

# Keywords that signal deep complexity
DEEP_KEYWORDS = [
    "architecture", "ארכיטקטורה", "system", "מערכת", "launch", "השקה",
    "pivot", "פיבוט", "compare", "להשוות", "tradeoff", "deep dive",
    "migration", "מיגרציה", "rewrite", "לשכתב", "redesign",
]


def categorize_message(text: str) -> dict:
    """Analyze user message and return category, complexity, and mentioned tools.

    Returns:
        {
            "category": str,     # architecture, feature, business, security, ux, integration, strategy, other
            "complexity": str,   # quick, medium, deep
            "tools_mentioned": list[str]
        }
    """
    text_lower = text.lower()
    word_count = len(text.split())

    # --- Category detection ---
    scores: dict[str, int] = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[category] = score

    category = max(scores, key=scores.get) if scores else "other"

    # --- Complexity detection ---
    has_deep_keyword = any(kw in text_lower for kw in DEEP_KEYWORDS)

    if word_count >= 150 or has_deep_keyword:
        complexity = "deep"
    elif word_count < 50:
        complexity = "quick"
    else:
        complexity = "medium"

    # --- Tool detection ---
    tools_mentioned = []
    for tool_name, patterns in KNOWN_TOOLS:
        if any(p in text_lower for p in patterns):
            tools_mentioned.append(tool_name)

    return {
        "category": category,
        "complexity": complexity,
        "tools_mentioned": tools_mentioned,
    }
