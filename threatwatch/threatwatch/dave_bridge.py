"""D.A.V.E. integration point for THREATWATCH v1.

v1 is just a collector, not yet the v3 LLM analyst — but D.A.V.E. can still
use the raw data today. Drop this import into D.A.V.E.'s query.py (or
wherever tools/functions are registered) to give it a `threat_brief` tool
ahead of the real correlator/analyst phases:

    from threatwatch.dave_bridge import get_recent_brief
    # register get_recent_brief as a callable tool, or just call it directly
    # and inject the text into context the way web-search results are
    # injected today.

This deliberately returns plain text, not a prompt — keep the "retrieval is
source of truth" pattern from the rest of D.A.V.E.'s pipeline: paste this
into context, let the model summarize/respond, don't let it free-associate
the numbers.
"""
import datetime as dt

from . import db


def get_recent_brief(hours: int = 24) -> str:
    """Plain-text digest of new KEV entries, high-severity CVEs, and new
    vendor articles in the last `hours`. Returns '' if nothing new —
    callers should treat that as 'no brief needed', not an error.
    """
    # Format must match SQLite's own datetime('now') output ("YYYY-MM-DD HH:MM:SS",
    # space separator, no offset) — isoformat() uses a 'T' separator and a +00:00
    # suffix, which breaks the string comparisons below silently (space < 'T'
    # lexicographically, so every real row would look "older" than the cutoff).
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    lines = []

    with db.connect() as conn:
        kev_rows = conn.execute(
            "SELECT cve_id, vendor_project, product, vulnerability_name, date_added "
            "FROM kev WHERE first_seen >= ? ORDER BY date_added DESC",
            (cutoff,),
        ).fetchall()
        if kev_rows:
            lines.append(f"New CISA KEV entries (confirmed exploited in the wild) — {len(kev_rows)}:")
            for r in kev_rows:
                lines.append(
                    f"  - {r['cve_id']}: {r['vendor_project']} {r['product']} — {r['vulnerability_name']}"
                )

        cve_rows = conn.execute(
            "SELECT cve_id, cvss_v3_score, cvss_v3_severity, description FROM cves "
            "WHERE first_seen >= ? AND cvss_v3_score >= 8.0 "
            "ORDER BY cvss_v3_score DESC LIMIT 15",
            (cutoff,),
        ).fetchall()
        if cve_rows:
            lines.append(f"\nNew high-severity CVEs (CVSS >= 8.0) — {len(cve_rows)} (showing top 15):")
            for r in cve_rows:
                desc = (r["description"] or "")[:160]
                lines.append(f"  - {r['cve_id']} ({r['cvss_v3_score']} {r['cvss_v3_severity']}): {desc}")

        article_rows = conn.execute(
            "SELECT source, title, link FROM articles WHERE fetched_at >= ? "
            "ORDER BY fetched_at DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
        if article_rows:
            lines.append(f"\nNew vendor threat reporting — {len(article_rows)} (showing up to 20):")
            for r in article_rows:
                lines.append(f"  - [{r['source']}] {r['title']} — {r['link']}")

    return "\n".join(lines)


def get_group_profile(name_or_alias: str) -> str:
    """Look up a threat actor group by name or alias. Used for the
    'profile a threat group' write-ups from Phase 2 of the plan — gives
    D.A.V.E. (or you, directly) a starting reference instead of a blank page.
    """
    with db.connect() as conn:
        row = conn.execute(
            "SELECT name, aliases, description, country_hint FROM attack_groups "
            "WHERE name LIKE ? OR aliases LIKE ?",
            (f"%{name_or_alias}%", f"%{name_or_alias}%"),
        ).fetchone()
    if not row:
        return f"No ATT&CK group found matching '{name_or_alias}'. Run the mitre_attack collector first."
    return (
        f"{row['name']} (aliases: {row['aliases']})\n"
        f"Country hint: {row['country_hint'] or 'unknown'}\n\n"
        f"{row['description']}"
    )


if __name__ == "__main__":
    print(get_recent_brief(hours=24 * 7) or "(nothing new)")
