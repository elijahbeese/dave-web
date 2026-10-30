"""Collector: CISA Known Exploited Vulnerabilities (KEV) catalog.

Free, no API key, updated as CISA adds entries. This is the single highest
signal-to-noise source in the whole project: if something's in here, it's
confirmed exploited in the wild.
"""
import logging
import requests

from .. import config, db

log = logging.getLogger("threatwatch.cisa_kev")


def run() -> int:
    resp = requests.get(
        config.CISA_KEV_URL,
        timeout=config.HTTP_TIMEOUT,
        headers={"User-Agent": config.USER_AGENT},
    )
    resp.raise_for_status()
    payload = resp.json()
    vulns = payload.get("vulnerabilities", [])

    count = 0
    with db.connect() as conn:
        for v in vulns:
            cve_id = v.get("cveID")
            if not cve_id:
                continue
            conn.execute(
                """
                INSERT INTO kev (cve_id, vendor_project, product, vulnerability_name,
                                  date_added, short_description, required_action,
                                  due_date, known_ransomware_use, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(cve_id) DO UPDATE SET
                    vendor_project=excluded.vendor_project,
                    product=excluded.product,
                    vulnerability_name=excluded.vulnerability_name,
                    date_added=excluded.date_added,
                    short_description=excluded.short_description,
                    required_action=excluded.required_action,
                    due_date=excluded.due_date,
                    known_ransomware_use=excluded.known_ransomware_use,
                    last_seen=datetime('now')
                """,
                (
                    cve_id,
                    v.get("vendorProject"),
                    v.get("product"),
                    v.get("vulnerabilityName"),
                    v.get("dateAdded"),
                    v.get("shortDescription"),
                    v.get("requiredAction"),
                    v.get("dueDate"),
                    v.get("knownRansomwareCampaignUse"),
                ),
            )
            count += 1

    log.info("CISA KEV: upserted %d entries", count)
    db.log_run("cisa_kev", "ok", count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    run()
