"""Collector: NVD CVE API 2.0.

Pulls CVEs published/modified in the last THREATWATCH_NVD_LOOKBACK_DAYS days.
Works without an API key at a crawl (5 req/30s); set NVD_API_KEY for a much
higher rate limit — free, instant signup at nvd.nist.gov/developers.
"""
import datetime as dt
import json
import logging
import time

import requests

from .. import config, db

log = logging.getLogger("threatwatch.nvd_cve")


def _extract_cvss(metrics: dict):
    for key in ("cvssMetricV31", "cvssMetricV30"):
        entries = metrics.get(key)
        if entries:
            data = entries[0].get("cvssData", {})
            return data.get("baseScore"), data.get("baseSeverity")
    return None, None


def _extract_cwes(weaknesses: list):
    ids = []
    for w in weaknesses or []:
        for d in w.get("description", []):
            val = d.get("value")
            if val and val.startswith("CWE-"):
                ids.append(val)
    return ",".join(sorted(set(ids)))


def _store_cve_item(conn, cve: dict) -> bool:
    """Shared insert/upsert logic for one NVD CVE object -- used by both the
    bulk date-range pull (run()) and the targeted single-CVE lookup
    (fetch_single, used by the KEV publish-date backfill). Returns True if
    a row was written."""
    cve_id = cve.get("id")
    if not cve_id:
        return False
    descriptions = cve.get("descriptions", [])
    desc_en = next((d["value"] for d in descriptions if d.get("lang") == "en"), "")
    score, severity = _extract_cvss(cve.get("metrics", {}))
    cwes = _extract_cwes(cve.get("weaknesses", []))

    conn.execute(
        """
        INSERT INTO cves (cve_id, published, last_modified, description,
                           cvss_v3_score, cvss_v3_severity, cwe_ids,
                           source_identifier, raw_json, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(cve_id) DO UPDATE SET
            last_modified=excluded.last_modified,
            description=excluded.description,
            cvss_v3_score=excluded.cvss_v3_score,
            cvss_v3_severity=excluded.cvss_v3_severity,
            cwe_ids=excluded.cwe_ids,
            raw_json=excluded.raw_json,
            last_seen=datetime('now')
        """,
        (
            cve_id, cve.get("published"), cve.get("lastModified"), desc_en,
            score, severity, cwes, cve.get("sourceIdentifier"), json.dumps(cve),
        ),
    )
    return True


def fetch_single(conn, cve_id: str) -> bool:
    """Targeted lookup for one CVE by ID -- used to backfill just the KEV
    catalog's publish dates (so the zero-day heuristic has something to
    compare against) without bulk-pulling years of unrelated CVE history.
    Returns True on success, False if NVD had nothing for this ID (rare,
    but KEV occasionally references IDs NVD hasn't published yet)."""
    headers = {"User-Agent": config.USER_AGENT}
    if config.NVD_API_KEY:
        headers["apiKey"] = config.NVD_API_KEY
    resp = requests.get(
        config.NVD_CVE_API_URL, params={"cveId": cve_id}, headers=headers,
        timeout=config.HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    vulns = resp.json().get("vulnerabilities", [])
    if not vulns:
        return False
    return _store_cve_item(conn, vulns[0].get("cve", {}))


def run() -> int:
    # Filtered on *published* date, not lastModified — lastModified includes
    # routine re-enrichment of old CVEs (NVD touches thousands of old
    # records a week) and turns every run into a huge, slow pull. Published
    # date keeps this focused on what the project actually wants: new CVEs.
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=config.NVD_LOOKBACK_DAYS)
    params = {
        "pubStartDate": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "pubEndDate": end.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "resultsPerPage": 200,
        "startIndex": 0,
    }
    headers = {"User-Agent": config.USER_AGENT}
    if config.NVD_API_KEY:
        headers["apiKey"] = config.NVD_API_KEY

    count = 0
    with db.connect() as conn:
        while True:
            resp = requests.get(
                config.NVD_CVE_API_URL,
                params=params,
                headers=headers,
                timeout=config.HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()

            for item in payload.get("vulnerabilities", []):
                if _store_cve_item(conn, item.get("cve", {})):
                    count += 1

            total_results = payload.get("totalResults", 0)
            params["startIndex"] += params["resultsPerPage"]
            log.info("NVD CVE: fetched %d/%d", min(params["startIndex"], total_results), total_results)
            if params["startIndex"] >= total_results:
                break
            # Respect NVD rate limits between pages.
            time.sleep(6 if not config.NVD_API_KEY else 0.6)

    log.info("NVD CVE: upserted %d entries (last %d days)", count, config.NVD_LOOKBACK_DAYS)
    db.log_run("nvd_cve", "ok", count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    run()
