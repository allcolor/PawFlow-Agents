import sqlite3
from pawflow import pfp

# Runs relay-local (see PFP_DEVELOPER_GUIDE.md "Two trust boundaries"): plain
# stdlib sqlite3 against a relay-local path needs no allowed_tools grant.
# Never invents a deadline or a dossier status — whatever legal.db holds is
# what is shown, verbatim (guardrail: no fact presented that isn't sourced).

payload = pfp.payload or {}
args = payload.get("arguments", {}) if isinstance(payload, dict) else {}
db_path = str(args.get("db_path") or "/workspace/legal.db")

try:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    dossiers = [dict(r) for r in conn.execute(
        "SELECT id, client, statut, derniere_activite FROM dossiers "
        "ORDER BY derniere_activite DESC"
    ).fetchall()]
    delais = [dict(r) for r in conn.execute(
        "SELECT d.id, d.dossier_id, do.client, d.type_acte, d.date_butoir, "
        "d.confirme_par_avocate, "
        "CAST(julianday(d.date_butoir) - julianday('now') AS INTEGER) AS jours_restants "
        "FROM delais d JOIN dossiers do ON do.id = d.dossier_id "
        "WHERE d.statut = 'actif' ORDER BY d.date_butoir"
    ).fetchall()]
    conn.close()
    pfp.result({"dossiers": dossiers, "delais": delais, "db_path": db_path})
except sqlite3.OperationalError as exc:
    # Most likely legal-db-init hasn't been run yet on this db_path — surface
    # a clear, actionable error instead of a stack trace to the dashboard.
    pfp.result({
        "error": f"legal.db introuvable ou vide ({exc}). Lancez le flow "
                 "legal-db-init une fois, ou verifiez db_path.",
        "dossiers": [], "delais": [], "db_path": db_path,
    })
