#!/usr/bin/env python3
"""Standalone script -- run once/day via systemd timer (see
deploy/dave-daily-brief.service + .timer), NOT by daveweb itself on page
load. Generating a brief is a real LLM call through dave-api; doing that on
a schedule means the dashboard always has a brief ready instantly instead
of someone's first visit of the day eating a 30-120s wait.

Safe to also run by hand any time:
    cd ~/daveweb && source ../threatwatch/venv/bin/activate  # or wherever your venv is
    python3 generate_daily_brief.py
"""
import logging
import os
import sys

sys.path.insert(0, os.path.expanduser("~/threatwatch"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from threatwatch import db as tw_db  # noqa: E402
from threatwatch.daily_brief import (  # noqa: E402
    build_brief_prompt, build_news_brief_prompt, save_brief, today_str, get_cached_brief,
    build_suggestions_prompt, parse_suggestions,
)

import dave_api  # noqa: E402

log = logging.getLogger("generate_daily_brief")


def _generate_one(kind: str, prompt_fn, force: bool):
    existing = get_cached_brief(today_str(), kind=kind)
    if existing and not force:
        log.info("[%s] Brief for %s already generated at %s -- skipping.",
                  kind, today_str(), existing["generated_at"])
        return
    log.info("[%s] Asking D.A.V.E. for today's brief...", kind)
    brief = dave_api.generate_daily_brief(prompt_fn())
    suggestions_raw = dave_api.generate_daily_brief(build_suggestions_prompt(brief))
    suggestions = parse_suggestions(suggestions_raw)
    save_brief(brief, model="dave-api", kind=kind, suggestions=suggestions)
    log.info("[%s] Saved brief for %s (%d chars, %d suggested questions).",
              kind, today_str(), len(brief), len(suggestions))


def main():
    logging.basicConfig(level=logging.INFO)
    tw_db.init_db()
    force = "--force" in sys.argv
    _generate_one("threat", build_brief_prompt, force)
    _generate_one("news", build_news_brief_prompt, force)


if __name__ == "__main__":
    main()
