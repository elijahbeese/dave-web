"""Dedicated zero-day view -- separate from relevance.py's asset-matched
"what to worry about" list. This one is broader on purpose: every KEV entry
CISA has ever added, flagged by the same zero-day heuristic used on the
dashboard (relevance._zero_day_flag), with asset-match status attached so
you can still see at a glance which ones are actually about your gear vs.
just general situational awareness.
"""
from . import db
from .relevance import _match_asset, _zero_day_flag


def get_zero_days(limit: int = 50):
    """Returns KEV entries flagged as likely zero-days (exploited at or
    before public disclosure), most recently added first. `zero_day` is
    True/False/None per the same heuristic as relevance.py -- None means we
    don't have NVD publish-date data for that CVE to compare against yet,
    not that it's confirmed NOT a zero-day.
    """
    results = []
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM kev ORDER BY date_added DESC LIMIT 500"
        ).fetchall()
        for r in rows:
            flag = _zero_day_flag(conn, r["cve_id"], r["date_added"])
            if not flag:  # skip False and None -- this page is specifically the zero-day list
                continue
            haystack = " ".join(filter(None, [
                r["vendor_project"], r["product"], r["vulnerability_name"], r["short_description"],
            ]))
            results.append({
                "cve_id": r["cve_id"],
                "title": r["vulnerability_name"],
                "vendor_project": r["vendor_project"],
                "product": r["product"],
                "description": r["short_description"],
                "required_action": r["required_action"],
                "due_date": r["due_date"],
                "date_added": r["date_added"],
                "matched_asset": _match_asset(haystack),
            })
            if len(results) >= limit:
                break
    return results
