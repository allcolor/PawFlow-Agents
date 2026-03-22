#!/usr/bin/env python3
# PawFlow CLI

"""
Point d'entree principal de PawFlow.
Usage:
    python cli.py run <flow.json> [--input <file>] [--verbose]
    python cli.py validate <flow.json>
    python cli.py list-tasks
    python cli.py info <flow.json>
    python cli.py serve [--host] [--port] [--reload]
    python cli.py gui [--host] [--port] [--headless]
    python cli.py plugins list|install|remove
    python cli.py export <flow> [-o output]
    python cli.py import <file> [-o output]
    python cli.py cluster status [--api-url]
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Enregistrer toutes les tasks avant le parsing
import tasks  # noqa: F401 - declenche register_all_tasks()

from core import FlowFile, TaskFactory, FlowError
from engine.parser import FlowParser, FlowValidator
from engine.continuous_executor import ContinuousFlowExecutor
from engine.provenance import ProvenanceRepository


def cmd_run(args):
    """Executer un flow depuis un fichier JSON."""
    flow_path = Path(args.flow)
    if not flow_path.exists():
        print(f"ERREUR: Fichier introuvable: {flow_path}")
        return 1

    # Configurer le logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    logger = logging.getLogger('PawFlow')

    # Parser le flow
    logger.info(f"Chargement du flow: {flow_path}")
    try:
        flow = FlowParser.parse_from_file(str(flow_path))
    except Exception as e:
        print(f"ERREUR: Impossible de parser le flow: {e}")
        return 1

    logger.info(f"Flow: {flow.name} (id={flow.id})")
    logger.info(f"Tasks: {list(flow.tasks.keys())}")
    logger.info(f"Relations: {len(flow.relations)}")

    # Valider
    errors = FlowValidator.validate(flow, strict=False)
    if errors:
        for err in errors:
            logger.warning(f"Validation: {err}")

    # Creer les FlowFiles d'entree
    input_flowfiles = []
    if args.input:
        for input_path in args.input:
            p = Path(input_path)
            if not p.exists():
                print(f"ERREUR: Fichier d'entree introuvable: {p}")
                return 1
            content = p.read_bytes()
            ff = FlowFile(
                content=content,
                attributes={
                    'filename': p.name,
                    'path': str(p.parent),
                    'absolute.path': str(p.resolve()),
                    'fileSize': str(len(content)),
                }
            )
            input_flowfiles.append(ff)
            logger.info(f"Input: {p.name} ({len(content)} bytes)")
    else:
        # FlowFile vide par defaut
        input_flowfiles = [FlowFile(content=b'', attributes={'filename': 'stdin'})]
        logger.info("Pas d'input specifie, utilisation d'un FlowFile vide")

    # Provenance
    repo = ProvenanceRepository() if args.provenance else None

    # Parse --param overrides
    param_overrides = {}
    if args.param:
        for p in args.param:
            if '=' not in p:
                print(f"ERREUR: Format invalide pour --param: '{p}' (attendu KEY=VALUE)")
                return 1
            k, v = p.split('=', 1)
            param_overrides[k.strip()] = v.strip()
        logger.info(f"Parameter overrides: {param_overrides}")

    # Execute (batch mode via ContinuousFlowExecutor)
    logger.info("--- Debut de l'execution ---")
    start = time.time()
    result = ContinuousFlowExecutor.run_batch(
        flow,
        input_flowfiles=input_flowfiles,
        parameters=param_overrides if param_overrides else None,
        max_workers=args.workers,
        max_retries=args.retries,
        timeout=args.timeout or 300,
        provenance=repo,
    )
    elapsed = time.time() - start

    # Afficher le resultat
    print()
    if result.success:
        print(f"SUCCES - {flow.name}")
    else:
        print(f"ECHEC - {flow.name}")

    print(f"  Duree: {elapsed:.3f}s")
    print(f"  FlowFiles en sortie: {len(result.output_flowfiles)}")

    if result.errors:
        print(f"  Erreurs:")
        for err in result.errors:
            print(f"    - {err.get('error', 'unknown')}")

    # Stats par task
    if result.task_statistics:
        print(f"  Statistiques par tache:")
        for tid, stats in result.task_statistics.items():
            print(f"    {tid} ({stats.task_type}): "
                  f"{stats.success_count} ok, {stats.error_count} err, "
                  f"{stats.avg_duration_ms:.1f}ms avg")

    # Provenance
    if repo and repo.size() > 0:
        prov = repo.to_dict()
        print(f"  Provenance: {prov['total_events']} evenements")
        for etype, count in prov['events_by_type'].items():
            print(f"    {etype}: {count}")

    # Output
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, ff in enumerate(result.output_flowfiles):
            fname = ff.get_attribute('filename', f'output_{i}.dat')
            out_path = out_dir / fname
            out_path.write_bytes(ff.content)
            logger.info(f"Output: {out_path}")
        print(f"  Fichiers ecrits dans: {out_dir}")

    return 0 if result.success else 1


def cmd_validate(args):
    """Valider un flow JSON."""
    flow_path = Path(args.flow)
    if not flow_path.exists():
        print(f"ERREUR: Fichier introuvable: {flow_path}")
        return 1

    try:
        flow = FlowParser.parse_from_file(str(flow_path))
        errors = FlowValidator.validate(flow, strict=False)
    except Exception as e:
        print(f"ERREUR: {e}")
        return 1

    if errors:
        print(f"VALIDATION ECHOUEE pour {flow.name}:")
        for err in errors:
            print(f"  - {err}")
        return 1
    else:
        print(f"VALIDE: {flow.name} ({len(flow.tasks)} tasks, {len(flow.relations)} relations)")
        return 0


def cmd_list_tasks(args):
    """Lister toutes les tasks disponibles."""
    print("Tasks disponibles:")
    print(f"{'TYPE':<25} {'NOM':<30} {'VERSION'}")
    print("-" * 65)
    for task_type in sorted(TaskFactory.list_types()):
        cls = TaskFactory.get(task_type)
        print(f"{cls.TYPE:<25} {cls.NAME:<30} {cls.VERSION}")


def cmd_info(args):
    """Afficher les infos d'un flow."""
    flow_path = Path(args.flow)
    if not flow_path.exists():
        print(f"ERREUR: Fichier introuvable: {flow_path}")
        return 1

    try:
        flow = FlowParser.parse_from_file(str(flow_path))
    except Exception as e:
        print(f"ERREUR: {e}")
        return 1

    print(f"Flow: {flow.name}")
    print(f"  ID: {flow.id}")
    print(f"  Version: {flow.version}")
    print(f"  Description: {flow.description}")
    print(f"  Tasks ({len(flow.tasks)}):")
    for tid, task in flow.tasks.items():
        print(f"    - {tid} (type={task.TYPE})")
    print(f"  Relations ({len(flow.relations)}):")
    for rel in flow.relations:
        print(f"    - {rel['from']} -> {rel['to']}")
    return 0


def cmd_serve(args):
    """Start the PawFlow API server."""
    try:
        import uvicorn
    except ImportError:
        print("ERREUR: uvicorn n'est pas installe. Installez-le avec: pip install uvicorn")
        return 1
    try:
        from api.app import app
    except ImportError as e:
        print(f"ERREUR: Impossible d'importer l'API: {e}")
        return 1
    uvicorn.run(app, host=args.host, port=args.port, log_level="info",
                reload=args.reload)


def cmd_gui(args):
    """Start PawFlow server with optional Streamlit GUI.

    The PawFlow server (HTTP :9090, chat, agents) runs in the main process.
    Streamlit admin GUI is launched as an optional subprocess — if it fails
    or is unavailable, the server continues running.
    """
    import signal
    import subprocess
    import threading

    project_root = os.path.dirname(os.path.abspath(__file__))

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )
    logger = logging.getLogger("pawflow")

    # 1. Register tasks and restore flows in the main process
    logger.info("Registering tasks...")
    from tasks import register_all_tasks
    register_all_tasks()

    logger.info("Restoring deployed flows...")
    from gui.services.executor_registry import ExecutorRegistry
    er = ExecutorRegistry.get_instance()
    er.restore_from_disk()
    n = er.count()
    logger.info(f"PawFlow server ready — {n} flow(s) restored, chat at http://{args.host}:{args.port}/chat")

    # 2. Launch Streamlit as optional subprocess (non-blocking)
    streamlit_proc = None
    st_port = int(args.port) + 1  # Streamlit on port+1 (e.g. 9091)

    def _launch_streamlit():
        nonlocal streamlit_proc
        try:
            env = os.environ.copy()
            pythonpath = env.get("PYTHONPATH", "")
            if project_root not in pythonpath.split(os.pathsep):
                env["PYTHONPATH"] = project_root + (os.pathsep + pythonpath if pythonpath else "")

            st_argv = [
                sys.executable, "-m", "streamlit", "run", "gui/main.py",
                f"--server.port={st_port}", f"--server.address={args.host}",
                "--server.headless=true",
            ]
            streamlit_proc = subprocess.Popen(
                st_argv, env=env, cwd=project_root,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            logger.info(f"Streamlit admin GUI at http://{args.host}:{st_port}")
        except Exception as e:
            logger.warning(f"Streamlit not available (server continues without admin GUI): {e}")

    if not args.headless:
        threading.Thread(target=_launch_streamlit, daemon=True).start()

    # 3. Keep main thread alive, handle Ctrl+C gracefully
    def _shutdown(sig, frame):
        logger.info("Shutting down...")
        if streamlit_proc:
            try:
                streamlit_proc.terminate()
            except Exception:
                pass
        # Stop all executors
        try:
            reg = ExecutorRegistry.get_instance()
            for eid in list(reg._executors.keys()):
                try:
                    reg._executors[eid].stop()
                except Exception:
                    pass
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        # Block main thread (the server runs in background threads)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown(None, None)


def cmd_plugins(args):
    """Manage plugins."""
    from core.plugin import PluginManager
    pm = PluginManager()

    if args.action == "list":
        plugins = pm.list_plugins()
        if not plugins:
            print("No plugins installed.")
            return 0
        for p in plugins:
            print(f"  {p['id']} v{p.get('version', '?')} — {p.get('description', '')}")
    elif args.action == "install":
        if not args.path:
            print("ERREUR: --path is required for install.")
            return 1
        result = pm.install(args.path)
        print(f"Installed: {result.id}")
    elif args.action == "remove":
        if not args.plugin_id:
            print("ERREUR: --plugin-id is required for remove.")
            return 1
        pm.uninstall(args.plugin_id)
        print(f"Removed: {args.plugin_id}")
    elif args.action == "upgrade":
        if not args.plugin_id:
            print("ERREUR: --plugin-id is required for upgrade.")
            return 1
        try:
            result = pm.upgrade(args.plugin_id, args.version)
            print(f"Upgraded: {result.id} to v{result.version}")
        except Exception as e:
            print(f"ERREUR: {e}")
            return 1
    elif args.action == "downgrade":
        if not args.plugin_id:
            print("ERREUR: --plugin-id is required for downgrade.")
            return 1
        if not args.version:
            print("ERREUR: --version is required for downgrade.")
            return 1
        try:
            result = pm.downgrade(args.plugin_id, args.version)
            print(f"Downgraded: {result.id} to v{result.version}")
        except Exception as e:
            print(f"ERREUR: {e}")
            return 1
    elif args.action == "history":
        if not args.plugin_id:
            print("ERREUR: --plugin-id is required for history.")
            return 1
        history = pm.get_plugin_history(args.plugin_id)
        if not history:
            print(f"No version history for plugin '{args.plugin_id}'.")
            return 0
        print(f"Version history for '{args.plugin_id}':")
        for entry in history:
            ts = entry.get("timestamp", "?")
            action = entry.get("action", "?")
            version = entry.get("version", "?")
            prev = entry.get("previous_version")
            if prev:
                print(f"  [{ts}] {action}: {prev} -> {version}")
            else:
                print(f"  [{ts}] {action}: {version}")
    elif args.action == "info":
        if not args.plugin_id:
            print("ERREUR: --plugin-id is required for info.")
            return 1
        version = pm.get_installed_version(args.plugin_id)
        if version is None:
            print(f"Plugin '{args.plugin_id}' is not installed.")
            return 1
        loaded = pm.get_plugin(args.plugin_id)
        print(f"Plugin: {args.plugin_id}")
        print(f"  Version: {version}")
        if loaded:
            desc = loaded.descriptor
            print(f"  Name: {desc.name}")
            print(f"  Author: {desc.author}")
            print(f"  Description: {desc.description}")
            print(f"  Tasks: {loaded.loaded_tasks}")
            print(f"  Services: {loaded.loaded_services}")
            print(f"  Flows: {loaded.loaded_flows}")
        available = pm.list_versions(args.plugin_id)
        if available:
            print(f"  Available versions: {', '.join(available)}")
        deps = pm.check_dependencies(args.plugin_id)
        if deps.get("details"):
            print(f"  Dependencies satisfied: {deps['satisfied']}")
            for dep_id, info in deps["details"].items():
                status = "OK" if info.get("satisfied") else "MISSING"
                print(f"    {dep_id}: {info.get('required', '?')} [{status}]")
        history = pm.get_plugin_history(args.plugin_id)
        if history:
            last = history[-1]
            print(f"  Last action: {last['action']} v{last['version']} at {last['timestamp']}")
    return 0



def cmd_export(args):
    """Export a flow as .pfp plugin."""
    flow_path = Path(args.flow_path)
    if not flow_path.exists():
        print(f"ERREUR: Fichier introuvable: {flow_path}")
        return 1
    try:
        with open(flow_path) as f:
            flow_config = json.load(f)
    except Exception as e:
        print(f"ERREUR: Impossible de lire le flow: {e}")
        return 1

    from core.plugin import export_flow_as_plugin
    output = args.output or f"{flow_path.stem}.pfp"
    export_flow_as_plugin(flow_config, output)
    print(f"Exported: {output}")
    return 0


def cmd_import_flow(args):
    """Import a NiFi flow or .pfp plugin."""
    path = args.path
    if path.endswith('.pfp'):
        from core.plugin import PluginManager
        pm = PluginManager()
        result = pm.install(path)
        print(f"Imported plugin: {result.id}")
    else:
        from engine.nifi_converter import NiFiConverter
        with open(path) as f:
            content = f.read()
        converter = NiFiConverter()
        result = converter.convert(content)

        output_path = args.output or "flows/imported_flow.json"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(result.flow, f, indent=2)

        print(f"Imported flow: {output_path}")
        if result.warnings:
            print(f"Warnings ({len(result.warnings)}):")
            for w in result.warnings[:5]:
                print(f"  - {w}")
        if result.subflows:
            for sf in result.subflows:
                sf_path = f"flows/{sf.get('id', 'subflow')}.json"
                with open(sf_path, 'w') as f_out:
                    json.dump(sf, f_out, indent=2)
                print(f"  Subflow: {sf_path}")
    return 0


def cmd_triggers(args):
    """Manage event triggers."""
    from engine.triggers import TriggerManager, TriggerType

    tm = TriggerManager()
    config_path = "config/triggers.json"
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
            with urllib.request.urlopen(f"{url}/api/v1/system/cluster/status") as resp:
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


def main():
    parser = argparse.ArgumentParser(
        prog='pawflow',
        description='PawFlow (Open Cuddle Edition) - Data workflow framework'
    )
    subparsers = parser.add_subparsers(dest='command', help='Commande')

    # run
    run_parser = subparsers.add_parser('run', help='Executer un flow')
    run_parser.add_argument('flow', help='Fichier JSON du flow')
    run_parser.add_argument('--input', '-i', nargs='+', help='Fichiers d\'entree')
    run_parser.add_argument('--output-dir', '-o', help='Repertoire de sortie')
    run_parser.add_argument('--verbose', '-v', action='store_true', help='Mode verbose')
    run_parser.add_argument('--provenance', '-p', action='store_true', help='Activer la provenance')
    run_parser.add_argument('--workers', '-w', type=int, default=4, help='Workers paralleles (defaut: 4)')
    run_parser.add_argument('--retries', '-r', type=int, default=3, help='Max retries (defaut: 3)')
    run_parser.add_argument('--timeout', '-t', type=int, default=300, help='Timeout en secondes (defaut: 300)')
    run_parser.add_argument('--param', action='append', metavar='KEY=VALUE',
                            help='Override un parametre du flow (repeatable)')

    # validate
    val_parser = subparsers.add_parser('validate', help='Valider un flow')
    val_parser.add_argument('flow', help='Fichier JSON du flow')

    # list-tasks
    subparsers.add_parser('list-tasks', help='Lister les tasks disponibles')

    # info
    info_parser = subparsers.add_parser('info', help='Infos sur un flow')
    info_parser.add_argument('flow', help='Fichier JSON du flow')

    # serve
    serve_parser = subparsers.add_parser('serve', help='Start the PawFlow API server')
    serve_parser.add_argument('--host', default='0.0.0.0', help='Host to bind (default: 0.0.0.0)')
    serve_parser.add_argument('--port', type=int, default=8000, help='Port (default: 8000)')
    serve_parser.add_argument('--reload', action='store_true', help='Enable auto-reload')

    # gui
    gui_parser = subparsers.add_parser('gui', help='Start the PawFlow Streamlit GUI')
    gui_parser.add_argument('--host', default='localhost', help='Host (default: localhost)')
    gui_parser.add_argument('--port', type=int, default=8501, help='Port (default: 8501)')
    gui_parser.add_argument('--headless', action='store_true', help='Run in headless mode')

    # plugins
    plugins_parser = subparsers.add_parser('plugins', help='Manage plugins')
    plugins_parser.add_argument('action',
                                choices=['list', 'install', 'remove',
                                         'upgrade', 'downgrade', 'history', 'info'],
                                help='Plugin action')
    plugins_parser.add_argument('--path', help='Path to .pfp file (for install)')
    plugins_parser.add_argument('--plugin-id', help='Plugin ID')
    plugins_parser.add_argument('--version', help='Target version (for upgrade/downgrade)')

    # export
    export_parser = subparsers.add_parser('export', help='Export a flow as .pfp plugin')
    export_parser.add_argument('flow_path', help='Flow JSON file to export')
    export_parser.add_argument('-o', '--output', help='Output .pfp path')

    # import
    import_parser = subparsers.add_parser('import', help='Import a NiFi flow or .pfp plugin')
    import_parser.add_argument('path', help='File to import (.pfp, .xml, or .json)')
    import_parser.add_argument('-o', '--output', help='Output flow JSON path')

    # triggers
    triggers_parser = subparsers.add_parser('triggers', help='Manage event triggers')
    triggers_parser.add_argument('action',
                                 choices=['list', 'create', 'start', 'stop', 'delete', 'history', 'run'],
                                 help='Trigger action')
    triggers_parser.add_argument('--trigger-id', dest='trigger_id', help='Trigger ID')
    triggers_parser.add_argument('--trigger-type', dest='trigger_type',
                                 choices=['file_watcher', 'webhook', 'event', 'polling'],
                                 help='Trigger type (for create)')
    triggers_parser.add_argument('--flow-path', dest='flow_path', help='Flow JSON path (for create)')
    triggers_parser.add_argument('--name', help='Trigger name (for create)')
    triggers_parser.add_argument('--config', help='Trigger config as JSON string (for create)')

    # cluster
    cluster_parser = subparsers.add_parser('cluster', help='Cluster management')
    cluster_parser.add_argument('action', choices=['status'], help='Cluster action')
    cluster_parser.add_argument('--api-url', dest='api_url', help='API URL (default: http://localhost:8000)')

    # re-embed-memories
    reembed_parser = subparsers.add_parser('re-embed-memories',
                                            help='Re-embed all memories with vector embeddings')
    reembed_parser.add_argument('--user-id', dest='user_id', required=True, help='User ID')
    reembed_parser.add_argument('--provider', choices=['openai', 'local', 'auto'],
                                default='auto', help='Embedding provider (default: auto)')
    reembed_parser.add_argument('--api-key', dest='api_key', help='OpenAI API key (optional, uses env var)')

    args = parser.parse_args()

    if args.command == 'run':
        sys.exit(cmd_run(args))
    elif args.command == 'validate':
        sys.exit(cmd_validate(args))
    elif args.command == 'list-tasks':
        cmd_list_tasks(args)
    elif args.command == 'info':
        sys.exit(cmd_info(args))
    elif args.command == 'serve':
        sys.exit(cmd_serve(args))
    elif args.command == 'gui':
        sys.exit(cmd_gui(args))
    elif args.command == 'plugins':
        sys.exit(cmd_plugins(args))
    elif args.command == 'export':
        sys.exit(cmd_export(args))
    elif args.command == 'import':
        sys.exit(cmd_import_flow(args))
    elif args.command == 'triggers':
        sys.exit(cmd_triggers(args))
    elif args.command == 'cluster':
        sys.exit(cmd_cluster(args))
    elif args.command == 're-embed-memories':
        sys.exit(cmd_re_embed(args))
    else:
        parser.print_help()


if __name__ == '__main__':
    # Windows + Python 3.14: Playwright/Patchright needs ProactorEventLoopPolicy
    # for subprocess support.  Without this, scrapling cleanup crashes on exit.
    import sys, warnings
    if sys.platform == "win32":
        import asyncio
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    main()
