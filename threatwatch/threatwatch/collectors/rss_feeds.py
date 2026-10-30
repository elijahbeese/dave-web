"""Collector: vendor threat blog RSS feeds.

Each feed is isolated — one vendor's broken/moved URL doesn't take down the
whole collector run. Check the ingestion_log table (or stdout) after the
first few runs and fix dead URLs in config.RSS_FEEDS as needed.
"""
import logging
import feedparser
import requests

from .. import config, db

log = logging.getLogger("threatwatch.rss_feeds")


def run() -> int:
    total = 0
    with db.connect() as conn:
        for source_name, url in config.RSS_FEEDS.items():
            try:
                resp = requests.get(
                    url,
                    timeout=config.HTTP_TIMEOUT,
                    headers={"User-Agent": config.USER_AGENT},
                    allow_redirects=True,
                )
                resp.raise_for_status()
                parsed = feedparser.parse(resp.content)
            except Exception as exc:  # noqa: BLE001 - one bad feed shouldn't kill the run
                log.warning("RSS feed failed for %s (%s): %s", source_name, url, exc)
                db.log_run("rss_feeds", "warn", 0, f"{source_name}: {exc}")
                continue

            feed_count = 0
            for entry in parsed.entries:
                link = entry.get("link")
                title = entry.get("title")
                if not link or not title:
                    continue
                summary = entry.get("summary", "")[:2000]
                published = entry.get("published", entry.get("updated", ""))
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO articles (source, title, link, summary, published)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (source_name, title, link, summary, published),
                    )
                    feed_count += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to store article from %s: %s", source_name, exc)

            log.info("%s: %d entries parsed", source_name, feed_count)
            total += feed_count

    db.log_run("rss_feeds", "ok", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    run()
