# THREATWATCH

AI threat intel feed, built as a module for D.A.V.E. Part of the [Threat
Intel Pivot Plan](./docs/Threat_Intel_Pivot_Plan.md) (Nov 2026 – Dec 2027).

Pulls CVEs, confirmed-exploited vulnerabilities, ATT&CK groups/techniques,
and vendor threat reporting into one local SQLite database. This is **v1
(Collector)** — it ingests raw data on a schedule. v2 (Correlator) links
CVEs to actors/techniques/countries; v3 (Analyst) adds a local LLM summary
layer; v4 (Brief) wires it into D.A.V.E.'s voice/GUI as a morning brief.

## Sources (all free)

| Source | What it gives you | API key needed |
|---|---|---|
| CISA KEV catalog | Confirmed exploited-in-the-wild CVEs | No |
| NVD CVE API 2.0 | Full CVE database, scored | Optional (higher rate limit) |
| MITRE ATT&CK (Enterprise) | Threat actor groups + techniques | No |
| Vendor blogs (RSS) | Mandiant, Microsoft, Unit 42, CrowdStrike, Recorded Future | No |
| AlienVault OTX | Community threat pulses | Yes (free account) |

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env — NVD_API_KEY and OTX_API_KEY are both optional but recommended

set -a; source .env; set +a
python3 -m threatwatch.run_collectors
```

First run takes a few minutes (NVD without an API key is rate-limited to
5 requests / 30s). Get a free NVD key here — it's instant:
https://nvd.nist.gov/developers/request-an-api-key

Check what landed:

```bash
sqlite3 data/threatwatch.db "select count(*) from kev;"
sqlite3 data/threatwatch.db "select count(*) from cves;"
sqlite3 data/threatwatch.db "select collector, status, records_in, run_at from ingestion_log order by run_at desc limit 10;"
```

## Deploying on yourhost

1. `git clone` this into wherever D.A.V.E.'s other modules live (alongside
   the existing `jarvis_v2` repo structure, or as a sibling project it
   imports from).
2. Point `THREATWATCH_DB_PATH` in `.env` at the dave-storage pool, e.g.
   `/mnt/dave-storage/threatwatch/threatwatch.db` — so it survives box
   rebuilds and other services (v2/v3 later) can read it too.
3. Copy `deploy/threatwatch.service` and `deploy/threatwatch.timer` into
   `/etc/systemd/system/`, fix the `WorkingDirectory`/`User`/venv path to
   match your actual layout, then:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now threatwatch.timer
   systemctl list-timers threatwatch.timer   # confirm it's scheduled
   ```
   Default schedule: every 4 hours, 5 min after boot. Adjust
   `OnUnitActiveSec` in the timer file if you want it tighter/looser.

## Wiring into D.A.V.E. today (ahead of v3)

v1 doesn't have an LLM analyst yet, but there's no reason D.A.V.E. can't use
the raw data now. `threatwatch/dave_bridge.py` has two functions ready to
import directly into `query.py` or wherever tools get registered:

```python
from threatwatch.dave_bridge import get_recent_brief, get_group_profile

get_recent_brief(hours=24)          # plain-text digest: new KEV entries,
                                     # high-severity CVEs, new vendor articles
get_group_profile("Sandworm")       # ATT&CK group lookup by name or alias
```

Keep the same pattern the rest of D.A.V.E. uses: this returns plain text to
inject into context, not a prompt — retrieval stays the source of truth,
the model summarizes what's actually in the DB instead of improvising.

This alone covers the Phase 2 "profile a threat group" write-up task from
the plan — `get_group_profile("Sandworm")` or `get_group_profile("APT28")`
gives you a sourced starting point instead of a blank page.

## Schema

See `threatwatch/db.py` for the full SQLite schema. Six tables: `kev`,
`cves`, `attack_groups`, `attack_techniques`, `articles`, `otx_pulses`, plus
an `ingestion_log` for run history/debugging. All plain SQLite — no ORM, no
new dependency needed to read it from the existing D.A.V.E. pipeline.

## Known rough edges (fix as you hit them)

- RSS feed URLs drift. Microsoft's returned a 403 in testing (likely a
  bot-blocking WAF rule, not a dead feed) — if `rss_feeds` keeps failing
  for a given vendor, check `ingestion_log` for the actual error and either
  fix the URL or drop it from `config.RSS_FEEDS`.
- The ATT&CK group → country mapping in `mitre_attack.py` is a crude
  alias-matching heuristic, not real attribution. Good enough to flag
  "probably Russia/China/Iran/DPRK" for the Phase 2 write-up, not good
  enough to cite without checking the actual group description.
- No dedup/fuzzy-matching between sources yet (e.g. the same incident
  covered by both Mandiant and Unit 42) — that's v2 (Correlator) territory.

## Roadmap (from the pivot plan)

- **v1 — Collector** (this): scheduled ingestion into SQLite. ✅
- **v2 — Correlator**: link CVEs to actors/techniques/countries.
- **v3 — Analyst**: local LLM summarizes new reporting, flags what matters.
- **v4 — Brief**: D.A.V.E. delivers a spoken/written morning brief + simple
  trend dashboard.
