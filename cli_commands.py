"""Secondary PawFlow CLI commands (triggers, cluster, re-embed, admin user).

Extracted from ``cli.py`` to keep each file under the 800-line limit. These
commands are wired into the argparse dispatcher in ``cli.py`` via
``set_defaults(func=...)`` and re-exported there for import stability
(``cli.cmd_admin_user``, ``cli.cmd_cluster`` are referenced by tests).
"""

import getpass
import json
import logging
import os
import time


def cmd_triggers(args):
    """Manage event triggers."""
    from engine.triggers import TriggerManager, TriggerType

    tm = TriggerManager()
    from core.paths import TRIGGERS_FILE
    config_path = str(TRIGGERS_FILE)
    tm.load_triggers(config_path)

    if args.action == "list":
        triggers = tm.list_triggers()
        if not triggers:
            print("No triggers configured.")
            return 0
        print(f"{'ID':<20} {'TYPE':<15} {'STATE':<10} {'FIRES':<8} {'FLOW'}")
        print("-" * 75)
        for t in triggers:
            print(f"{t['trigger_id']:<20} {t['type']:<15} {t['state']:<10} "
                  f"{t['fire_count']:<8} {t['flow_path']}")

    elif args.action == "create":
        if not args.trigger_id or not args.trigger_type or not args.flow_path:
            print("ERREUR: --trigger-id, --trigger-type, and --flow-path are required.")
            return 1
        config = {}
        if args.config:
            try:
                config = json.loads(args.config)
            except json.JSONDecodeError as e:
                print(f"ERREUR: Invalid JSON for --config: {e}")
                return 1
        try:
            result = tm.create_trigger(
                trigger_id=args.trigger_id,
                trigger_type=TriggerType(args.trigger_type),
                flow_path=args.flow_path,
                name=args.name or args.trigger_id,
                config=config,
                enabled=True,
            )
            tm.save_triggers(config_path)
            print(f"Created trigger: {args.trigger_id} ({result['state']})")
        except ValueError as e:
            print(f"ERREUR: {e}")
            return 1

    elif args.action == "start":
        if not args.trigger_id:
            print("ERREUR: --trigger-id is required.")
            return 1
        try:
            result = tm.start_trigger(args.trigger_id)
            print(f"Started: {args.trigger_id} ({result['state']})")
        except ValueError as e:
            print(f"ERREUR: {e}")
            return 1

    elif args.action == "stop":
        if not args.trigger_id:
            print("ERREUR: --trigger-id is required.")
            return 1
        try:
            result = tm.stop_trigger(args.trigger_id)
            tm.save_triggers(config_path)
            print(f"Stopped: {args.trigger_id}")
        except ValueError as e:
            print(f"ERREUR: {e}")
            return 1

    elif args.action == "delete":
        if not args.trigger_id:
            print("ERREUR: --trigger-id is required.")
            return 1
        removed = tm.delete_trigger(args.trigger_id)
        if removed:
            tm.save_triggers(config_path)
            print(f"Deleted: {args.trigger_id}")
        else:
            print(f"Trigger '{args.trigger_id}' not found.")
            return 1

    elif args.action == "history":
        history = tm.get_history(
            trigger_id=args.trigger_id if args.trigger_id else None,
            limit=50,
        )
        if not history:
            print("No trigger history.")
            return 0
        for h in history:
            from datetime import datetime
            ts = datetime.fromtimestamp(h["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
            status = "OK" if h["flow_executed"] else "FAIL"
            event = h["event_data"].get("event", "?")
            error = f" | {h['error']}" if h["error"] else ""
            print(f"  [{ts}] {h['trigger_id']} [{status}] {event}{error}")

    elif args.action == "run":
        # Keep running with triggers active (like scheduler start)
        if not tm.list_triggers():
            print("No triggers to run. Create some first.")
            return 0
        print(f"Running {len(tm.list_triggers())} trigger(s). Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            tm.stop_all()
            print("\nTriggers stopped.")

    return 0


def cmd_cluster(args):
    """Cluster management."""
    if args.action == "status":
        import urllib.request
        url = args.api_url or "http://localhost:8000"
        try:
            with urllib.request.urlopen(f"{url}/api/v1/system/cluster/status") as resp:  # nosec B310
                data = json.loads(resp.read())
                if data.get("cluster_enabled"):
                    status = data["status"]
                    print(f"Cluster: {status.get('total_instances', 0)} instances")
                    print(f"Role: {status.get('role', 'unknown')}")
                    print(f"Coordinator: {status.get('coordinator_host', 'none')}")
                    for inst in status.get("instances", []):
                        marker = " *" if inst.get("id") == status.get("instance_id") else ""
                        print(f"  {inst['id']} [{inst['role']}] {inst['host']}{marker}")
                else:
                    print("Cluster mode not enabled.")
        except Exception as e:
            print(f"Cannot reach API at {url}: {e}")
    return 0


def cmd_re_embed(args):
    """Re-embed all memories for a user with vector embeddings."""
    user_id = args.user_id
    provider = args.provider or "auto"
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    from core.embeddings import EmbeddingProvider
    from core.memory_store import MemoryStore

    store = MemoryStore.instance()
    ep = EmbeddingProvider.instance()

    count = store.count(user_id)
    if count == 0:
        print(f"No memories found for user '{user_id}'.")
        return 0

    print(f"Re-embedding {count} memories for user '{user_id}' (provider: {provider})...")

    def embed_fn(text: str):
        results = ep.embed([text], provider=provider, api_key=api_key)
        return results[0] if results else []

    embedded = store.re_embed_all(user_id, embed_fn)
    print(f"Done. {embedded}/{count} memories re-embedded.")
    return 0


def _read_admin_password(args) -> str:
    password = args.password or ""
    if args.password_env:
        password = os.environ.get(args.password_env, "")
    if not password:
        password = getpass.getpass("Admin password: ")
        confirm = getpass.getpass("Confirm admin password: ")
        if password != confirm:
            print("ERROR: passwords do not match")
            return ""
    if not password:
        print("ERROR: password is required")
        return ""
    return password


def cmd_admin_user(args):
    """Create or repair a local PawFlow admin account."""
    from core.security import SecurityManager, Role

    username = (args.username or "").strip()
    if not username:
        print("ERROR: username is required")
        return 1

    password = _read_admin_password(args)
    if not password:
        return 1

    sm = SecurityManager.get_instance()
    if args.action == "create":
        existing = sm.get_user(username)
        if existing:
            sm.update_user(
                username,
                role=Role.ADMIN,
                password=password,
                enabled=True,
                email=args.email or None,
                display_name=args.display_name or None,
            )
            print(f"Admin user '{username}' updated and enabled.")
            return 0
        sm.create_user(
            username,
            password,
            Role.ADMIN,
            email=args.email or "",
            display_name=args.display_name or username,
        )
        print(f"Admin user '{username}' created.")
        return 0

    if args.action == "reset-password":
        user = sm.get_user(username)
        if not user:
            print(f"ERROR: user '{username}' does not exist")
            return 1
        sm.update_user(username, role=Role.ADMIN, password=password, enabled=True)
        print(f"Admin user '{username}' password reset and account enabled.")
        return 0

    print(f"ERROR: unsupported admin-user action '{args.action}'")
    return 1
