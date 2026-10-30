"""Your homelab's asset list, for matching incoming CVEs/KEV entries against
what you actually run. Plain Python list, not a database table, on purpose —
this changes rarely and you edit it by hand when your hardware/software
changes, not through a UI.

Sourced from DAVE_Homelab_Architecture (Sept 27, 2026) plus confirmed via
chat. Update this file and redeploy whenever your inventory changes --
nothing else in THREATWATCH needs to change to pick up new assets.

Each entry: (display_name, [keywords], icon). A CVE/KEV row is considered a
match if ANY keyword appears (case-insensitive) in:
  - the KEV entry's vendor_project/product fields, or
  - the CVE's CPE criteria strings (from raw_json), or
  - as a last resort, the free-text description (noisier, so matches there
    alone are not treated as strongly as a CPE hit -- see relevance.py).

Keep keywords specific enough to avoid false positives ("Pi" would match
all sorts of unrelated things; "raspberry pi" is specific).

`icon` is a plain emoji, not a fetched brand logo -- deliberately. A pulled
SVG from some CDN slug is a silent broken-image risk on a page you might be
reading mid-incident; an emoji always renders, same dark-theme-safe way,
zero network dependency.
"""

ASSETS = [
    ("FortiGate 100D (firewall/router)", [
        "fortinet", "fortigate", "fortios",
    ], "🔥"),
    ("Netgear GS748Tv5 (switch)", [
        "netgear", "gs748",
    ], "🔌"),
    ("Dell PowerVault NX3200 / TrueNAS", [
        "truenas", "freenas", "ixsystems", "powervault",
    ], "💾"),
    ("HP ProLiant (DL380e / DL360 Gen10)", [
        "proliant", "hpe ilo", "hp ilo", "hewlett packard enterprise",
    ], "🖥️"),
    ("Proxmox VE (hypervisor)", [
        "proxmox",
    ], "🗄️"),
    ("Kali Linux (OSCP range)", [
        "kali linux", "offensive security",
    ], "🐉"),
    ("Raspberry Pi 5", [
        "raspberry pi", "raspberry pi os", "raspbian",
    ], "🍓"),
    ("Ubuntu (yourhost host OS)", [
        "ubuntu linux", "canonical ubuntu",
    ], "🐧"),
    ("macOS (admin MacBook Air)", [
        "macos", "mac os x", "apple macos",
    ], "🍎"),
    ("Tailscale", [
        "tailscale",
    ], "🔒"),
    ("Xfinity / Comcast gateway", [
        "xfinity", "comcast",
    ], "📡"),
    ("Ollama (D.A.V.E.'s LLM runtime)", [
        "ollama",
    ], "🦙"),
    ("ChromaDB (D.A.V.E.'s vector store)", [
        "chromadb", "chroma vector",
    ], "🧮"),
]

# criticality -> emoji, used on finding cards/detail pages/charts legends
SEVERITY_ICONS = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "⚪",
}
ZERO_DAY_ICON = "⚡"
NEWS_ICON = "📰"


def all_keywords():
    """Flat (keyword, display_name) pairs for matching, longest keywords
    first so "raspberry pi os" matches before a hypothetical bare "pi"."""
    pairs = []
    for display_name, keywords, _icon in ASSETS:
        for kw in keywords:
            pairs.append((kw.lower(), display_name))
    return sorted(pairs, key=lambda p: -len(p[0]))


def icon_for(display_name: str) -> str:
    for name, _keywords, icon in ASSETS:
        if name == display_name:
            return icon
    return "🛡️"  # fallback for anything not in the list (shouldn't normally happen)
