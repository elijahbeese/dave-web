"""Cache for D.A.V.E.-written mitigation/patch guidance per CVE.

Generation itself happens in daveweb (it's the thing with the live
connection to dave-api.service) -- this module is just the read/write
layer so the result is cached after the first generation instead of
re-prompting the model every time the dashboard loads.
"""
from . import db


def get_cached(cve_id: str):
    with db.connect() as conn:
        row = conn.execute(
            "SELECT guidance, model, generated_at FROM mitigations WHERE cve_id = ?",
            (cve_id,),
        ).fetchone()
    return dict(row) if row else None


def save(cve_id: str, guidance: str, model: str = ""):
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO mitigations (cve_id, guidance, model, generated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(cve_id) DO UPDATE SET
                guidance=excluded.guidance,
                model=excluded.model,
                generated_at=datetime('now')
            """,
            (cve_id, guidance, model),
        )
