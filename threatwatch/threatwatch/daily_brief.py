"""Daily brief: one LLM call/day that reads everything THREATWATCH pulled in
(relevant findings, zero-days, recent news) and writes a plain-English
summary, cached by date so the dashboard never blocks on an LLM call on
page load -- generation happens once, on a schedule (see
daveweb/generate_daily_brief.py + its systemd timer), not per visit.
"""
import datetime as dt
import json
import re

from . import config, db
from .assets import ASSETS
from .relevance import get_relevant_findings
from .zerodays import get_zero_days


def today_str() -> str:
    return dt.date.today().isoformat()


def get_cached_brief(brief_date: str = None, kind: str = "threat"):
    brief_date = brief_date or today_str()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT brief, model, generated_at, suggestions FROM daily_briefs "
            "WHERE brief_date = ? AND kind = ?",
            (brief_date, kind),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    # Briefs saved before the brief-chat feature existed have suggestions=NULL;
    # treat that the same as "none generated yet" rather than erroring.
    try:
        d["suggestions"] = json.loads(d["suggestions"]) if d.get("suggestions") else []
    except (TypeError, ValueError):
        d["suggestions"] = []
    return d


def save_brief(brief: str, model: str = "", brief_date: str = None, kind: str = "threat",
                suggestions=None):
    brief_date = brief_date or today_str()
    suggestions_json = json.dumps(suggestions) if suggestions is not None else "[]"
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_briefs (brief_date, kind, brief, model, generated_at, suggestions)
            VALUES (?, ?, ?, ?, datetime('now'), ?)
            ON CONFLICT(brief_date, kind) DO UPDATE SET
                brief=excluded.brief, model=excluded.model, generated_at=datetime('now'),
                suggestions=excluded.suggestions
            """,
            (brief_date, kind, brief, model, suggestions_json),
        )


def build_suggestions_prompt(brief_text: str) -> str:
    """Ask D.A.V.E. for a handful of natural follow-up questions a reader
    might have after hearing this specific brief -- these become clickable
    chips in the brief-chat box, so someone can dig into 'the CVE you
    mentioned' or 'that ransomware group' without typing it out themselves.
    """
    return (
        "Here is a news brief I just generated:\n\n"
        f"=== BRIEF ===\n{brief_text}\n=== END BRIEF ===\n\n"
        "Write exactly 4 short follow-up questions a listener might want to ask about "
        "something SPECIFIC mentioned in this brief -- a named CVE, company, group, "
        "person, place, or event.\n\n"
        "HARD RULE: every question must reference a name, term, or event that appears "
        "literally, word-for-word, in the BRIEF text above. Do not invent or assume any "
        "company, country, product, or incident that isn't actually named in that text, "
        "even if it sounds like the kind of thing that could plausibly be in a cyber "
        "news brief -- pull the subject of each question directly from the text, don't "
        "generate a plausible-sounding one from general knowledge. If you can't find 4 "
        "distinct real things worth asking about, write fewer questions rather than "
        "making one up.\n\n"
        "One question per line, each line starting with '- ', nothing else in the "
        "response -- no intro, no numbering, no closing remark."
    )


def parse_suggestions(raw_text: str, limit: int = 4) -> list:
    """Pull '- question' lines out of a suggestions-prompt response. Tolerant of
    the model using '*' or '1.' instead of '-', or adding stray blank lines --
    local models don't always follow list-formatting instructions exactly."""
    questions = []
    for line in (raw_text or "").splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip().strip('"')
        if cleaned and len(cleaned) > 8:  # skip stray empty/near-empty lines
            questions.append(cleaned)
    return questions[:limit]


def build_followup_prompt(brief_label: str, brief_text: str, question: str,
                           history: list = None) -> str:
    """A chat turn scoped to one already-generated brief -- 'tell me more about
    the CVE you mentioned' only makes sense with the brief as context, so this
    always re-sends it rather than relying on the model to remember an earlier
    turn. `history` is the prior Q/A pairs from this same brief-chat session
    (list of {"question":..., "answer":...} dicts), oldest first, so a
    follow-up to a follow-up still has the earlier exchange in view."""
    history_block = ""
    if history:
        lines = ["", "Earlier questions about this same brief, for context:"]
        for turn in history:
            lines.append(f"Q: {turn.get('question', '')}")
            lines.append(f"A: {turn.get('answer', '')}")
        history_block = "\n".join(lines) + "\n"

    return (
        f"Here is the {brief_label} I generated earlier:\n\n"
        f"=== BRIEF ===\n{brief_text}\n=== END BRIEF ===\n"
        f"{history_block}\n"
        f"Follow-up question about this brief: {question}\n\n"
        "Answer the question directly using what you know -- the brief above is context "
        "for what's being asked about, not a limit on what you're allowed to say; use "
        "your general knowledge and, if you have live search available, look up current "
        "detail the brief didn't include. If the question is about something that isn't "
        "actually in the brief, say so plainly rather than guessing what might have been "
        "meant. Keep the answer focused and conversational, not another full brief."
    )


def build_brief_prompt() -> str:
    """Assemble the day's material into a prompt D.A.V.E. can summarize.
    Deliberately bounded (top N of each) -- this is a prompt, not a dump."""
    relevant = get_relevant_findings(limit=10)
    zero_days = get_zero_days(limit=10)

    with db.connect() as conn:
        new_kev = conn.execute(
            "SELECT COUNT(*) AS n FROM kev WHERE date_added >= date('now', '-1 day')"
        ).fetchone()["n"]
        new_cve = conn.execute(
            "SELECT COUNT(*) AS n FROM cves WHERE published >= datetime('now', '-1 day')"
        ).fetchone()["n"]
        news_rows = conn.execute(
            "SELECT source, title FROM articles ORDER BY fetched_at DESC LIMIT 8"
        ).fetchall()

    lines = [
        f"New CISA KEV entries in the last 24h: {new_kev}",
        f"New tracked CVEs in the last 24h: {new_cve}",
        "",
        "Findings relevant to my homelab (asset-matched), most critical first:",
    ]
    if relevant:
        for f in relevant[:10]:
            lines.append(
                f"- [{f['criticality']}] {f['cve_id']} -- {f['matched_asset']} -- {f['title']}"
            )
    else:
        lines.append("- none right now")

    lines.append("")
    lines.append("Likely zero-days (exploited at/before public disclosure), any vendor:")
    if zero_days:
        for z in zero_days[:5]:
            tag = f" (MATCHES your {z['matched_asset']})" if z.get("matched_asset") else ""
            lines.append(f"- {z['cve_id']} -- {z['vendor_project']}/{z['product']}{tag}")
    else:
        lines.append("- none right now")

    lines.append("")
    lines.append("Recent headlines (vendor blogs + general cyber news):")
    for r in news_rows:
        lines.append(f"- [{r['source']}] {r['title']}")

    data_block = "\n".join(lines)

    return (
        "Write today's THREATWATCH daily brief for my homelab. Here's everything the "
        "collectors pulled in over the last day:\n\n"
        f"{data_block}\n\n"
        "Summarize this in plain English, 150-250 words. Lead with whether I actually need "
        "to do anything today (yes/no, and what) -- don't bury that. Mention the zero-days "
        "only if one actually matches my gear or is notably severe industry-wide; otherwise "
        "a one-line count is enough. Skip anything generic or padded -- if there's nothing "
        "urgent, say so plainly instead of manufacturing urgency. This gets read (or listened "
        "to) once a day, so make every sentence earn its place."
    )


def build_news_brief_prompt() -> str:
    """Separate from build_brief_prompt() on purpose -- that one is scoped
    to 'what matches my gear'. This one is the industry-wide picture: top
    stories regardless of match, with D.A.V.E. doing the relevance call on
    a per-story basis instead of the keyword-matcher pre-filtering anything
    out. Gives you both: the narrow actionable brief, and the broader
    situational one."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT source, title, summary, published FROM articles "
            "WHERE source IN ({}) ORDER BY fetched_at DESC LIMIT 20".format(
                ",".join("?" for _ in config.NEWS_SOURCES)
            ),
            tuple(config.NEWS_SOURCES),
        ).fetchall()

    asset_list = ", ".join(name.split(" (")[0] for name, _kw, _icon in ASSETS)

    lines = ["Top recent cyber/hacker news headlines (not pre-filtered to my gear):"]
    for r in rows:
        summary = (r["summary"] or "").strip().replace("\n", " ")[:200]
        lines.append(f"- [{r['source']}] {r['title']}{' -- ' + summary if summary else ''}")
    if not rows:
        lines.append("- no articles collected yet")
    data_block = "\n".join(lines)

    return (
        "Write today's broader cyber news brief -- NOT limited to what directly matches my "
        f"gear. My homelab assets, for reference, are: {asset_list}.\n\n"
        f"{data_block}\n\n"
        "Pick the 5-8 most significant stories from the list (biggest breaches, major "
        "vendor vulnerabilities, notable campaigns/actors, industry-shaping news). For each "
        "one: a one-to-two sentence summary, then a short, honest note on whether/how it "
        "relates to my specific setup -- if it genuinely doesn't apply to my gear, just say "
        "'not directly relevant to your setup' rather than stretching to manufacture a "
        "connection. Keep the whole thing to 250-350 words, plain English, no padding. This "
        "is the 'what's happening in the industry' brief, separate from my asset-specific "
        "threat brief -- don't just repeat that one."
    )
