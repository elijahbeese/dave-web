"""THREATWATCH v1 — run all collectors once.

Usage:
    python -m threatwatch.run_collectors

Intended to be run on a schedule (cron / systemd timer) on yourhost. One
collector failing (a dead RSS URL, NVD rate limit, OTX key not set) does not
stop the others — each is wrapped and logged independently.
"""
import logging
import sys

from . import db
from .collectors import cisa_kev, nvd_cve, mitre_attack, rss_feeds, otx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("threatwatch.run")

COLLECTORS = [
    ("CISA KEV", cisa_kev),
    ("NVD CVE", nvd_cve),
    ("MITRE ATT&CK", mitre_attack),
    ("RSS feeds", rss_feeds),
    ("OTX pulses", otx),
]


def main():
    db.init_db()
    results = {}
    failures = 0

    for label, module in COLLECTORS:
        log.info("--- Running collector: %s ---", label)
        try:
            count = module.run()
            results[label] = count
        except Exception as exc:  # noqa: BLE001 - keep going on failure
            log.error("Collector %s failed: %s", label, exc, exc_info=True)
            db.log_run(label.lower().replace(" ", "_"), "error", 0, str(exc))
            results[label] = f"FAILED: {exc}"
            failures += 1

    log.info("=== THREATWATCH collection run complete ===")
    for label, count in results.items():
        log.info("  %-15s %s", label, count)

    if failures:
        log.warning("%d collector(s) failed this run — check logs above.", failures)
        sys.exit(1)


if __name__ == "__main__":
    main()
