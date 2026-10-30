"""THREATWATCH configuration.

Everything here is overridable via environment variables so this drops into
D.A.V.E.'s existing .env-based setup without edits to source.
"""
import os
from pathlib import Path

# Where the SQLite DB lives. Point this at D.A.V.E.'s storage (the NX3200 /
# dave-storage pool) once this is deployed on yourhost — e.g.
# /mnt/dave-storage/threatwatch/threatwatch.db
DB_PATH = os.environ.get(
    "THREATWATCH_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "threatwatch.db"),
)

# --- CISA Known Exploited Vulnerabilities catalog ---
CISA_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"

# --- NVD CVE API 2.0 ---
# A free API key raises your rate limit from 5 req/30s to 50 req/30s.
# Get one at https://nvd.nist.gov/developers/request-an-api-key
NVD_API_KEY = os.environ.get("NVD_API_KEY", "")
NVD_CVE_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
# How many days back to pull on each run (NVD requires a bounded window for
# date-filtered queries; 120 days is the hard API max per request).
NVD_LOOKBACK_DAYS = int(os.environ.get("THREATWATCH_NVD_LOOKBACK_DAYS", "7"))

# --- MITRE ATT&CK (Enterprise) ---
MITRE_ATTACK_URL = "https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json"

# --- AlienVault OTX ---
# Free account + API key required: https://otx.alienvault.com/settings
OTX_API_KEY = os.environ.get("OTX_API_KEY", "")
OTX_PULSES_URL = "https://otx.alienvault.com/api/v1/pulses/subscribed"

# --- Vendor threat blogs (RSS) ---
# Feeds that 403/404 are skipped with a warning rather than crashing the run
# — vendors change these URLs without notice, so this list needs occasional
# maintenance. Check logs after the first few runs.
RSS_FEEDS = {
    "Mandiant": "https://www.mandiant.com/resources/blog/rss.xml",
    "Microsoft Security": "https://www.microsoft.com/en-us/security/blog/feed/",
    "Unit 42 (Palo Alto)": "https://unit42.paloaltonetworks.com/feed/",
    "CrowdStrike": "https://www.crowdstrike.com/en-us/blog/feed/",
    "Recorded Future": "https://www.recordedfuture.com/feed",
    # General cyber/hacker news, as opposed to the vendor advisory blogs
    # above -- NEWS_SOURCES below is what tells the dashboard/news page
    # which bucket a given source name belongs in.
    "The Hacker News": "https://feeds.feedburner.com/TheHackersNews",
    "BleepingComputer": "https://www.bleepingcomputer.com/feed/",
    "Krebs on Security": "https://krebsonsecurity.com/feed/",
    "The Record": "https://therecord.media/feed/",
    "SecurityWeek": "https://www.securityweek.com/feed/",
}

# Which of the RSS_FEEDS keys above are general news (shown on /news, used
# for "how does this relate to my situation" correlation) vs. vendor
# advisory blogs (still collected, still searchable, just not the thing
# the /news page leads with).
NEWS_SOURCES = {
    "The Hacker News",
    "BleepingComputer",
    "Krebs on Security",
    "The Record",
    "SecurityWeek",
}

HTTP_TIMEOUT = 20
USER_AGENT = "THREATWATCH/0.1 (personal threat-intel collector; contact: your-contact-info)"
