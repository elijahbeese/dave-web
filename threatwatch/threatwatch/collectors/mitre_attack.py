"""Collector: MITRE ATT&CK Enterprise (STIX bundle).

Pulls intrusion-set objects (= threat actor groups) and attack-pattern
objects (= techniques). This is the reference data v2 (Correlator) will
join CVEs and articles against — "who uses what."
"""
import logging
import requests

from .. import config, db

log = logging.getLogger("threatwatch.mitre_attack")

# Loose country hints by common group aliasing conventions. Not authoritative —
# flagged for v2's correlator to refine with actual CTI attribution, not just
# a naming heuristic.
_COUNTRY_HINTS = {
    "APT28": "Russia", "Fancy Bear": "Russia", "Sandworm": "Russia",
    "APT29": "Russia", "Cozy Bear": "Russia", "Turla": "Russia",
    "APT41": "China", "APT1": "China", "APT10": "China", "Volt Typhoon": "China",
    "Lazarus Group": "North Korea", "APT38": "North Korea", "Kimsuky": "North Korea",
    "APT33": "Iran", "APT34": "Iran", "MuddyWater": "Iran",
}


def _guess_country(name: str, aliases: list):
    for candidate in [name] + (aliases or []):
        if candidate in _COUNTRY_HINTS:
            return _COUNTRY_HINTS[candidate]
    return None


def run() -> int:
    resp = requests.get(
        config.MITRE_ATTACK_URL,
        timeout=config.HTTP_TIMEOUT,
        headers={"User-Agent": config.USER_AGENT},
    )
    resp.raise_for_status()
    bundle = resp.json()
    objects = bundle.get("objects", [])

    groups = [o for o in objects if o.get("type") == "intrusion-set" and not o.get("revoked")]
    techniques = [o for o in objects if o.get("type") == "attack-pattern" and not o.get("revoked")]

    count = 0
    with db.connect() as conn:
        for g in groups:
            aliases = g.get("aliases", [])
            conn.execute(
                """
                INSERT INTO attack_groups (stix_id, name, aliases, description, country_hint, last_seen)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(stix_id) DO UPDATE SET
                    name=excluded.name, aliases=excluded.aliases,
                    description=excluded.description, country_hint=excluded.country_hint,
                    last_seen=datetime('now')
                """,
                (
                    g["id"],
                    g.get("name"),
                    ",".join(aliases),
                    g.get("description", "")[:2000],
                    _guess_country(g.get("name", ""), aliases),
                ),
            )
            count += 1

        for t in techniques:
            tactic_phases = t.get("kill_chain_phases", [])
            tactic = ",".join(p.get("phase_name", "") for p in tactic_phases)
            ext_id = next(
                (r["external_id"] for r in t.get("external_references", [])
                 if r.get("source_name") == "mitre-attack"),
                None,
            )
            conn.execute(
                """
                INSERT INTO attack_techniques (stix_id, technique_id, name, tactic, description, last_seen)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(stix_id) DO UPDATE SET
                    technique_id=excluded.technique_id, name=excluded.name,
                    tactic=excluded.tactic, description=excluded.description,
                    last_seen=datetime('now')
                """,
                (
                    t["id"],
                    ext_id,
                    t.get("name"),
                    tactic,
                    t.get("description", "")[:2000],
                ),
            )
            count += 1

    log.info("MITRE ATT&CK: upserted %d groups + %d techniques", len(groups), len(techniques))
    db.log_run("mitre_attack", "ok", count)
    return count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    run()
