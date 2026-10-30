"""Match THREATWATCH's incoming CVE/KEV data against your actual homelab
assets (assets.py) and rank what's left by how worried you should be.

This is intentionally not full CPE-parsing precision -- it's a practical
first pass: substring match against the full NVD record (which includes
the CPE criteria strings that name affected vendors/products) catches real
hits without a schema migration. KEV entries don't carry CPE data the way
NVD CVEs do, so those match against CISA's own vendor/product/name fields
instead.

Criticality tiers (worst first):
  CRITICAL -- confirmed exploited in the wild (CISA KEV) AND matches your gear
  HIGH     -- CVSS >= 9 and matches your gear
  MEDIUM   -- CVSS 7.0-8.9 and matches your gear
  LOW      -- matches your gear but lower severity
A CVE that doesn't match anything you actually run is left out entirely --
this section is "what to worry about", not a general feed (the dashboard's
other sections already cover the firehose).
"""
import json

from . import db
from .assets import all_keywords

_CRITICALITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _match_asset(haystack: str):
    haystack = (haystack or "").lower()
    for kw, display_name in all_keywords():
        if kw in haystack:
            return display_name
    return None


def _zero_day_flag(conn, cve_id: str, kev_date_added: str):
    """Best-effort zero-day heuristic: if this CVE's NVD publish date is on
    or after the date CISA added it to KEV, it was being exploited
    at-or-before public disclosure -- that's the textbook zero-day pattern.
    Returns True/False/None (None = we don't have a published date for this
    CVE in our own `cves` table yet, so we can't tell either way -- NOT the
    same as "no", so the caller should render that as unknown, not safe).
    """
    if not kev_date_added:
        return None
    row = conn.execute(
        "SELECT published FROM cves WHERE cve_id = ?", (cve_id,)
    ).fetchone()
    if not row or not row["published"]:
        return None
    try:
        return row["published"][:10] >= kev_date_added[:10]
    except (TypeError, IndexError):
        return None


def get_related_articles(terms, limit: int = 3):
    """Find recent RSS articles (vendor blogs + general news, same table)
    whose title or summary mention any of `terms` (a CVE ID, an asset name,
    etc). Cheap LIKE-based matching -- no full-text index, this table isn't
    big enough to need one for a homelab dashboard.
    """
    terms = [t for t in (terms or []) if t]
    if not terms:
        return []
    clauses = []
    params = []
    for t in terms:
        clauses.append("(title LIKE ? OR summary LIKE ?)")
        params.extend([f"%{t}%", f"%{t}%"])
    where = " OR ".join(clauses)
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT source, title, link, published FROM articles "
            f"WHERE {where} ORDER BY fetched_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def static_checklist(criticality: str, is_kev: bool, zero_day):
    """Generic, non-LLM mitigation checklist -- the third guidance source
    alongside CISA's required_action and D.A.V.E.'s AI-written guidance.
    Deliberately boilerplate/tiered rather than CVE-specific; it's the
    "don't forget the basics" list, not a replacement for the other two.
    """
    steps = ["Confirm you're actually running the affected version/product before doing anything else."]
    if is_kev:
        steps.append("This is CONFIRMED exploited in the wild (CISA KEV) -- treat as active, not theoretical.")
    if zero_day:
        steps.append("Exploitation predates or coincides with public disclosure -- assume no advance warning was possible, check logs/history now, not just going forward.")
    if criticality in ("CRITICAL", "HIGH"):
        steps.append("Patch or upgrade immediately if a fix is available -- don't wait for a maintenance window.")
        steps.append("If you can't patch right now: restrict network exposure (firewall rule, VLAN isolation, disable the affected feature/service) as a stopgap.")
    else:
        steps.append("Patch or upgrade on your normal schedule -- no need to drop everything, but don't let it sit indefinitely either.")
    steps.append("After patching, verify the fix actually took (version check, re-scan) -- don't just assume the update applied cleanly.")
    steps.append("Review logs for the affected device/service around the KEV add date / CVE publish date for signs of prior compromise.")
    return steps


def get_relevant_findings(limit: int = 25, cve_lookback_days: int = 60):
    """Returns findings sorted most-critical first. Each is a dict with
    cve_id, criticality, title, matched_asset, is_kev, cvss_score,
    required_action, due_date, description, references, date_added.
    """
    findings = []

    with db.connect() as conn:
        kev_rows = conn.execute(
            "SELECT * FROM kev ORDER BY date_added DESC"
        ).fetchall()
        for r in kev_rows:
            haystack = " ".join(filter(None, [
                r["vendor_project"], r["product"], r["vulnerability_name"], r["short_description"],
            ]))
            matched = _match_asset(haystack)
            if not matched:
                continue
            findings.append({
                "cve_id": r["cve_id"],
                "criticality": "CRITICAL",
                "title": r["vulnerability_name"],
                "matched_asset": matched,
                "is_kev": True,
                "cvss_score": None,
                "required_action": r["required_action"],
                "due_date": r["due_date"],
                "description": r["short_description"],
                "references": [],
                "date_added": r["date_added"],
                "zero_day": _zero_day_flag(conn, r["cve_id"], r["date_added"]),
            })

        kev_ids = {f["cve_id"] for f in findings}

        cve_rows = conn.execute(
            "SELECT * FROM cves WHERE published >= datetime('now', ?) ORDER BY cvss_v3_score DESC",
            (f"-{cve_lookback_days} days",),
        ).fetchall()

        for r in cve_rows:
            if r["cve_id"] in kev_ids:
                continue  # already covered above at CRITICAL

            raw = r["raw_json"] or ""
            matched = _match_asset(raw) or _match_asset(r["description"])
            if not matched:
                continue

            score = r["cvss_v3_score"] or 0
            if score >= 9:
                tier = "HIGH"
            elif score >= 7:
                tier = "MEDIUM"
            else:
                tier = "LOW"

            references = []
            try:
                parsed = json.loads(raw)
                references = [
                    ref.get("url") for ref in parsed.get("references", [])[:5] if ref.get("url")
                ]
            except (json.JSONDecodeError, TypeError):
                pass

            findings.append({
                "cve_id": r["cve_id"],
                "criticality": tier,
                "title": r["cve_id"],
                "matched_asset": matched,
                "is_kev": False,
                "cvss_score": score,
                "required_action": None,
                "due_date": None,
                "description": (r["description"] or "")[:300],
                "references": references,
                "date_added": r["published"],
                "zero_day": None,  # CVE-only (non-KEV) rows have no exploited-in-wild date to compare against
            })

    findings.sort(key=lambda f: (_CRITICALITY_ORDER[f["criticality"]], -(f["cvss_score"] or 10)))
    return findings[:limit]


def get_chart_data(trend_days: int = 30):
    """Server-side aggregation for the dashboard's charts -- small enough
    result sets that there's no need for a separate JSON API, just embed
    directly in the dashboard template.
    """
    with db.connect() as conn:
        severity_rows = conn.execute(
            "SELECT cvss_v3_severity AS sev, COUNT(*) AS n FROM cves "
            "WHERE cvss_v3_severity IS NOT NULL GROUP BY cvss_v3_severity"
        ).fetchall()
        severity = {r["sev"]: r["n"] for r in severity_rows}

        trend_rows = conn.execute(
            "SELECT date(published) AS day, COUNT(*) AS n FROM cves "
            "WHERE published >= datetime('now', ?) GROUP BY date(published) ORDER BY day",
            (f"-{trend_days} days",),
        ).fetchall()
        trend = [{"day": r["day"], "n": r["n"]} for r in trend_rows if r["day"]]

        vendor_rows = conn.execute(
            "SELECT vendor_project AS vendor, COUNT(*) AS n FROM kev "
            "GROUP BY vendor_project ORDER BY n DESC LIMIT 8"
        ).fetchall()
        top_vendors = [{"vendor": r["vendor"], "n": r["n"]} for r in vendor_rows if r["vendor"]]

    return {"severity": severity, "trend": trend, "top_vendors": top_vendors}
