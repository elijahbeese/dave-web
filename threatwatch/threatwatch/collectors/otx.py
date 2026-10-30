"""Collector: AlienVault OTX subscribed pulses.

Requires a free OTX account + API key (OTX_API_KEY env var). Without a key
this collector logs a warning and no-ops rather than failing the whole run —
useful since it's the one source that's genuinely optional.
"""
import logging
import requests

from .. import config, db

log = logging.getLogger("threatwatch.otx")


def run() -> int:
    if not config.OTX_API_KEY:
        log.warning("OTX_API_KEY not set — skipping OTX collector. "
                     "Get a free key at https://otx.alienvault.com/settings")
        db.log_run("otx", "skipped", 0, "no API key")
        return 0

    headers = {
        "X-OTX-API-KEY": config.OTX_API_KEY,
        "User-Agent": config.USER_AGENT,
    }
    resp = requests.get(
        config.OTX_PULSES_URL,
        headers=headers,
        timeout=config.HTTP_TIMEOUT,
        params={"limit": 50},
    )
    resp.raise_for_status()
    payload = resp.json()
    pulses = payload.get("results", [])

    count = 0
    with db.connect() as conn:
        for p in pulses:
            conn.execute(
                """
                INSERT INTO otx_pulses (pulse_id, name, description, author, tags,
                                         indicator_count, created, modified, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(pulse_id) DO UPDATE SET
                    name=excluded.name, description=excluded.description,
                    tags=excluded.tags, indicator_count=excluded.indicator_count,
                    modified=excluded.modified, fetched_at=datetime('now')
                """,
                (
                    p.get("id"),
                    p.get("name"),
                    (p.get("description") or "")[:2000],
                    (p.get("author_name") or ""),
                    ",".join(p.get("tags", [])),
                    len(p.get("indicators", [])),
                    p.get("created"),
                    p.get("modified"),
                ),
            )
            count += 1

    log.info("OTX: upserted %d pulses", count)
    db.log_run("otx", "ok", count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    run()
