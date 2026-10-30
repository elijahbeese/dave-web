#!/usr/bin/env python3
"""One-time(ish) backfill: fetch NVD publish dates for every CVE in your
CISA KEV catalog that isn't already in the `cves` table.

Why this exists: the zero-day heuristic (relevance.py/zerodays.py) needs a
CVE's NVD publish date to compare against CISA's "added to KEV" date. The
regular nvd_cve collector only pulls CVEs published in the last
NVD_LOOKBACK_DAYS days -- it has no reason to know about a CVE from 2022.
KEV, on the other hand, goes back to 2021 and has ~1700+ entries. Rather
than bulk-pulling years of irrelevant CVE history just to backfill ~1700
specific records, this does a targeted single-CVE lookup for exactly the
IDs that are actually in your KEV catalog and missing a published date.

Safe to re-run -- only fetches CVE IDs not already in `cves`. Takes a
while without an NVD_API_KEY (5 req/30s = ~6s/CVE => ~3 hours for 1700
IDs); with a free API key (50 req/30s) it's under 20 minutes. Get one at
https://nvd.nist.gov/developers/request-an-api-key and set NVD_API_KEY in
your .env.
"""
import logging
import sys
import time

from . import config, db
from .collectors.nvd_cve import fetch_single

log = logging.getLogger("threatwatch.backfill_kev_dates")


def run(limit: int = None) -> int:
    with db.connect() as conn:
        missing = conn.execute(
            "SELECT k.cve_id FROM kev k LEFT JOIN cves c ON k.cve_id = c.cve_id "
            "WHERE c.cve_id IS NULL ORDER BY k.date_added DESC"
        ).fetchall()
    missing_ids = [r["cve_id"] for r in missing]
    if limit:
        missing_ids = missing_ids[:limit]

    log.info("%d KEV entries missing an NVD publish date. Fetching...", len(missing_ids))
    sleep_s = 0.6 if config.NVD_API_KEY else 6.0
    fetched, not_found, errors = 0, 0, 0

    with db.connect() as conn:
        for i, cve_id in enumerate(missing_ids, 1):
            try:
                if fetch_single(conn, cve_id):
                    fetched += 1
                else:
                    not_found += 1
            except Exception as exc:  # noqa: BLE001 - one bad lookup shouldn't kill the run
                log.warning("Failed to fetch %s: %s", cve_id, exc)
                errors += 1
            if i % 25 == 0 or i == len(missing_ids):
                log.info("  %d/%d done (%d fetched, %d not found, %d errors)",
                          i, len(missing_ids), fetched, not_found, errors)
            if i < len(missing_ids):
                time.sleep(sleep_s)

    log.info("Backfill done: %d fetched, %d not found on NVD, %d errors.", fetched, not_found, errors)
    db.log_run("backfill_kev_dates", "ok", fetched)
    return fetched


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(limit=lim)
