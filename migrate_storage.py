#!/usr/bin/env python3
"""Migrate PawFlow storage from old flat layout to new repository/runtime/system layout.

Run from the project root:
    python migrate_storage.py [--dry-run] [--backup-dir data_backup]

Phase 1: Backup data/ to data_backup/
Phase 2: Create new directory structure
Phase 3: Migrate repository (split monolithic JSONs into 1-file-per-entity)
Phase 4: Migrate runtime
Phase 5: Migrate system
Phase 6: Migrate flows & templates
Phase 7: Cleanup
"""

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("migrate")

DATA = Path("data")
OLD_CONFIG = DATA / "config"

NEW_REPO = DATA / "repository"
NEW_RUNTIME = DATA / "runtime"
NEW_SYSTEM = DATA / "system"

GLOBAL_USER = "__global__"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Failed to read %s: %s", path, e)
        return {}


def _write_json(path: Path, data, dry_run: bool):
    if dry_run:
        log.info("  [DRY] Would write %s", path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _move(src: Path, dst: Path, dry_run: bool):
    if not src.exists():
        return
    if dry_run:
        log.info("  [DRY] Would move %s -> %s", src, dst)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _copy(src: Path, dst: Path, dry_run: bool):
    if not src.exists():
        return
    if dry_run:
        log.info("  [DRY] Would copy %s -> %s", src, dst)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


# ── Phase 1: Backup ────────────────────────────────────────────────

def phase1_backup(backup_dir: Path, dry_run: bool):
    log.info("Phase 1: Backup data/ -> %s", backup_dir)
    if backup_dir.exists():
        log.error("Backup dir %s already exists. Aborting.", backup_dir)
        sys.exit(1)
    if dry_run:
        log.info("  [DRY] Would copy data/ -> %s", backup_dir)
        return
    shutil.copytree(DATA, backup_dir)
    log.info("  Backed up to %s", backup_dir)


# ── Phase 2: Create structure ──────────────────────────────────────

def phase2_create_dirs(dry_run: bool):
    log.info("Phase 2: Create new directory structure")
    dirs = [
        NEW_REPO / "agents" / "global",
        NEW_REPO / "skills" / "global",
        NEW_REPO / "tasks" / "global",
        NEW_REPO / "flows" / "global",
        NEW_REPO / "mcps" / "global",
        NEW_REPO / "prompts" / "global",
        NEW_REPO / "services" / "global",
        NEW_REPO / "tools",
        NEW_RUNTIME / "conversations",
        NEW_RUNTIME / "deployments",
        NEW_RUNTIME / "files",
        NEW_RUNTIME / "memories",
        NEW_RUNTIME / "knowledge_graphs",
        NEW_RUNTIME / "plans",
        NEW_RUNTIME / "sessions" / "claude",
        NEW_RUNTIME / "graphs",
        NEW_RUNTIME / "spill",
        NEW_SYSTEM,
    ]
    for d in dirs:
        if dry_run:
            log.info("  [DRY] mkdir %s", d)
        else:
            d.mkdir(parents=True, exist_ok=True)


# ── Phase 3: Migrate repository ────────────────────────────────────

def _split_monolithic(rtype: str, src_file: Path, dry_run: bool):
    """Split a monolithic JSON (key='user:name' -> value) into individual files."""
    data = _read_json(src_file)
    if not data:
        log.info("  %s: empty or missing, skipping", src_file)
        return 0

    count = 0
    for key, entry in data.items():
        # Parse key: "__global__:name" or "user_id:name"
        if ":" in key:
            user_id, name = key.split(":", 1)
        elif "." in key:
            # Old format: "user_id.name"
            user_id, name = key.split(".", 1)
        else:
            user_id = GLOBAL_USER
            name = key

        # Sanitize name for filesystem
        safe_name = name.replace("/", "_").replace("\\", "_")

        if user_id == GLOBAL_USER:
            dst = NEW_REPO / rtype / "global" / f"{safe_name}.json"
        else:
            dst = NEW_REPO / rtype / "users" / user_id / f"{safe_name}.json"

        entry_out = dict(entry)
        entry_out["name"] = name
        _write_json(dst, entry_out, dry_run)
        count += 1

    log.info("  %s: split %d entries from %s", rtype, count, src_file.name)
    return count


def phase3_migrate_repository(dry_run: bool):
    log.info("Phase 3: Migrate repository (split monolithic JSONs)")

    _split_monolithic("agents", OLD_CONFIG / "agents.json", dry_run)
    _split_monolithic("skills", OLD_CONFIG / "skills.json", dry_run)
    _split_monolithic("mcps", OLD_CONFIG / "mcp_servers.json", dry_run)
    _split_monolithic("prompts", OLD_CONFIG / "prompts.json", dry_run)
    _split_monolithic("tasks", OLD_CONFIG / "task_defs.json", dry_run)

    # Services: global_services.json -> repository/services/global/
    global_svc = _read_json(OLD_CONFIG / "global_services.json")
    for sid, sdef in global_svc.items():
        safe = sid.replace("/", "_")
        dst = NEW_REPO / "services" / "global" / f"{safe}.json"
        _write_json(dst, sdef, dry_run)
    if global_svc:
        log.info("  services: split %d global services", len(global_svc))

    # Services: user_services/{uid}.json -> repository/services/users/{uid}/
    user_svc_dir = OLD_CONFIG / "user_services"
    if user_svc_dir.exists():
        for f in user_svc_dir.glob("*.json"):
            uid = f.stem
            user_svcs = _read_json(f)
            for sid, sdef in user_svcs.items():
                safe = sid.replace("/", "_")
                dst = NEW_REPO / "services" / "users" / uid / f"{safe}.json"
                _write_json(dst, sdef, dry_run)
            if user_svcs:
                log.info("  services: split %d services for user %s",
                         len(user_svcs), uid)

    # Dynamic tools -> repository/tools/
    old_tools = DATA / "dynamic_tools"
    if old_tools.exists():
        for user_dir in old_tools.iterdir():
            if user_dir.is_dir():
                dst = NEW_REPO / "tools" / "users" / user_dir.name
                _copy(user_dir, dst, dry_run)
                log.info("  tools: copied %s", user_dir.name)


# ── Phase 4: Migrate runtime ──────────────────────────────────────

def phase4_migrate_runtime(dry_run: bool):
    log.info("Phase 4: Migrate runtime")

    dir_copies = [
        (DATA / "conversations",    NEW_RUNTIME / "conversations"),
        (DATA / "files",            NEW_RUNTIME / "files"),
        (DATA / "memories",         NEW_RUNTIME / "memories"),
        (DATA / "knowledge_graphs", NEW_RUNTIME / "knowledge_graphs"),
        (DATA / "plans",            NEW_RUNTIME / "plans"),
        (DATA / "claude_sessions",  NEW_RUNTIME / "sessions" / "claude"),
        (DATA / "graphs",           NEW_RUNTIME / "graphs"),
    ]

    for src, dst in dir_copies:
        if src.exists() and src.is_dir():
            _copy(src, dst, dry_run)
            log.info("  Copied %s -> %s", src, dst)

    # Deployments: split into 1 file per deploy
    old_deploys = DATA / "deployments"
    if old_deploys.exists():
        deploy_dst = NEW_RUNTIME / "deployments"
        for scope_dir in old_deploys.iterdir():
            if scope_dir.is_dir():
                for f in scope_dir.glob("*.json"):
                    deploy_data = _read_json(f)
                    if deploy_data:
                        deploy_id = f.stem
                        out = NEW_RUNTIME / "deployments" / f"{deploy_id}.json"
                        # Add owner scope info
                        deploy_data.setdefault("id", deploy_id)
                        deploy_data.setdefault("owner_scope", scope_dir.name)
                        _write_json(out, deploy_data, dry_run)
                log.info("  Deployments: split %s", scope_dir.name)
            elif scope_dir.suffix == ".json":
                # Direct file in deployments/
                _copy(scope_dir, deploy_dst / scope_dir.name, dry_run)

    # Single files
    file_copies = [
        (DATA / "token_usage.json",      NEW_RUNTIME / "token_usage.json"),
        (DATA / "identity_mappings.json", NEW_RUNTIME / "identity_mappings.json"),
        (DATA / "gateway_bans.json",     NEW_RUNTIME / "gateway_bans.json"),
    ]
    # Poll schedule
    old_poll = DATA / "poll_schedule" / "schedule.json"
    if old_poll.exists():
        file_copies.append((old_poll, NEW_RUNTIME / "poll_schedule.json"))

    for src, dst in file_copies:
        if src.exists():
            _copy(src, dst, dry_run)
            log.info("  Copied %s -> %s", src.name, dst)


# ── Phase 5: Migrate system ────────────────────────────────────────

def phase5_migrate_system(dry_run: bool):
    log.info("Phase 5: Migrate system")

    file_moves = [
        (OLD_CONFIG / "users.json",              NEW_SYSTEM / "users.json"),
        (OLD_CONFIG / "sessions.json",           NEW_SYSTEM / "sessions.json"),
        (OLD_CONFIG / "security.json",           NEW_SYSTEM / "security.json"),
        (OLD_CONFIG / "secret.key",              NEW_SYSTEM / "secret.key"),
        (DATA / "server_id",                     NEW_SYSTEM / "server_id"),
        (OLD_CONFIG / "agent_secrets.json",       NEW_SYSTEM / "agent_secrets.json"),
        (OLD_CONFIG / "global_parameters.json",   NEW_SYSTEM / "global_parameters.json"),
        (OLD_CONFIG / "global_secrets.json",      NEW_SYSTEM / "global_secrets.json"),
        (OLD_CONFIG / "llm_profiles.json",        NEW_SYSTEM / "llm_profiles.json"),
        (OLD_CONFIG / "task_templates.json",      NEW_SYSTEM / "task_templates.json"),
    ]

    for src, dst in file_moves:
        if src.exists():
            _copy(src, dst, dry_run)
            log.info("  Copied %s", src.name)

    # SSL dir
    old_ssl = OLD_CONFIG / "ssl"
    if old_ssl.exists():
        _copy(old_ssl, NEW_SYSTEM / "ssl", dry_run)
        log.info("  Copied ssl/")

    # User config dirs (secrets, params, oauth per user)
    old_users = OLD_CONFIG / "users"
    if old_users.exists():
        _copy(old_users, NEW_SYSTEM / "users", dry_run)
        log.info("  Copied users/ config")


# ── Phase 6: Migrate flows & templates ─────────────────────────────

def phase6_migrate_flows(dry_run: bool):
    log.info("Phase 6: Migrate flows & templates")

    # flows/*.json -> repository/flows/global/default/{flowname}/versions/1.0.0.json
    flows_dir = Path("flows")
    if flows_dir.exists():
        for f in flows_dir.glob("*.json"):
            flow_id = f.stem
            flow_data = _read_json(f)
            if not flow_data:
                continue

            # Add FQN fields
            flow_data["fqn"] = f"default.{flow_id}:1.0.0"
            flow_data["package"] = "default"
            flow_data["name"] = flow_id
            flow_data["version"] = "1.0.0"

            ver_path = (NEW_REPO / "flows" / "global" / "default" /
                        flow_id / "versions" / "1.0.0.json")
            _write_json(ver_path, flow_data, dry_run)

            latest_path = (NEW_REPO / "flows" / "global" / "default" /
                           flow_id / "latest.json")
            _write_json(latest_path, {"version": "1.0.0"}, dry_run)

        # package.json for default package
        pkg_path = NEW_REPO / "flows" / "global" / "default" / "package.json"
        _write_json(pkg_path, {
            "name": "default",
            "description": "Migrated flows",
            "author": "",
        }, dry_run)
        log.info("  Migrated %d flows into default package",
                 len(list(flows_dir.glob("*.json"))))

    # templates/*.json -> repository/flows/global/templates/{name}/versions/1.0.0.json
    templates_dir = Path("templates")
    if templates_dir.exists():
        for f in templates_dir.glob("*.json"):
            tpl_id = f.stem
            tpl_data = _read_json(f)
            if not tpl_data:
                continue

            tpl_data["fqn"] = f"templates.{tpl_id}:1.0.0"
            tpl_data["package"] = "templates"
            tpl_data["name"] = tpl_id
            tpl_data["version"] = "1.0.0"

            ver_path = (NEW_REPO / "flows" / "global" / "templates" /
                        tpl_id / "versions" / "1.0.0.json")
            _write_json(ver_path, tpl_data, dry_run)

            latest_path = (NEW_REPO / "flows" / "global" / "templates" /
                           tpl_id / "latest.json")
            _write_json(latest_path, {"version": "1.0.0"}, dry_run)

        pkg_path = NEW_REPO / "flows" / "global" / "templates" / "package.json"
        _write_json(pkg_path, {
            "name": "templates",
            "description": "Migrated templates",
            "author": "",
        }, dry_run)
        log.info("  Migrated %d templates",
                 len(list(templates_dir.glob("*.json"))))

    # agent_flows/*.json and agent_templates/*.json -> user scope
    # These were created by agents in conversations — always conv scope,
    # but we don't have the original conv_id. Put in user scope (promotable later).
    _owner = "quentin.anciaux"

    for extra_dir, label in [(DATA / "agent_flows", "agent flows"),
                              (DATA / "agent_templates", "agent templates")]:
        if extra_dir.exists():
            count = 0
            for f in extra_dir.glob("*.json"):
                flow_id = f.stem
                flow_data = _read_json(f)
                if not flow_data:
                    continue
                flow_data["fqn"] = f"default.{flow_id}:1.0.0"
                flow_data["package"] = "default"
                flow_data["name"] = flow_id
                flow_data["version"] = "1.0.0"

                ver_path = (NEW_REPO / "flows" / "users" / _owner / "default" /
                            flow_id / "versions" / "1.0.0.json")
                if not ver_path.exists() or dry_run:
                    _write_json(ver_path, flow_data, dry_run)
                    latest_path = (NEW_REPO / "flows" / "users" / _owner / "default" /
                                   flow_id / "latest.json")
                    _write_json(latest_path, {"version": "1.0.0"}, dry_run)
                    count += 1
            if count:
                # package.json for user's default package
                pkg_path = NEW_REPO / "flows" / "users" / _owner / "default" / "package.json"
                if not pkg_path.exists() or dry_run:
                    _write_json(pkg_path, {
                        "name": "default",
                        "description": f"Migrated flows for {_owner}",
                        "author": _owner,
                    }, dry_run)
                log.info("  Migrated %d %s into user:%s/default package",
                         count, label, _owner)


# ── Phase 7: Summary ───────────────────────────────────────────────

def phase7_summary(dry_run: bool):
    prefix = "[DRY RUN] " if dry_run else ""
    log.info("")
    log.info("%sMigration complete.", prefix)
    log.info("")
    if not dry_run:
        log.info("Old directories still exist (not deleted):")
        for d in [OLD_CONFIG, DATA / "conversations", DATA / "deployments",
                  DATA / "files", DATA / "memories", DATA / "knowledge_graphs",
                  DATA / "plans", DATA / "claude_sessions", DATA / "graphs",
                  DATA / "dynamic_tools", DATA / "agent_flows",
                  DATA / "agent_templates", DATA / "poll_schedule",
                  Path("flows"), Path("templates")]:
            if d.exists():
                log.info("  %s (can be deleted after verification)", d)
        log.info("")
        log.info("New structure:")
        log.info("  %s", NEW_REPO)
        log.info("  %s", NEW_RUNTIME)
        log.info("  %s", NEW_SYSTEM)


# ── Main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Migrate PawFlow storage layout")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without modifying anything")
    parser.add_argument("--backup-dir", default="data_backup",
                        help="Backup directory (default: data_backup)")
    args = parser.parse_args()

    if not DATA.exists():
        log.error("data/ directory not found. Run from project root.")
        sys.exit(1)

    log.info("PawFlow Storage Migration")
    log.info("=========================")
    if args.dry_run:
        log.info("DRY RUN - no changes will be made\n")

    phase1_backup(Path(args.backup_dir), args.dry_run)
    phase2_create_dirs(args.dry_run)
    phase3_migrate_repository(args.dry_run)
    phase4_migrate_runtime(args.dry_run)
    phase5_migrate_system(args.dry_run)
    phase6_migrate_flows(args.dry_run)
    phase7_summary(args.dry_run)


if __name__ == "__main__":
    main()
