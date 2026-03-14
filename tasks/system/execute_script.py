# Execute Script Task

"""
Tâche ExecuteScript - Exécuter un script Python sur le contenu d'un FlowFile.

Uses the unified sandbox from core.sandbox:
- Safe builtins whitelist
- Module whitelist (json, re, csv, datetime, io, requests, etc.)
- Sandboxed open() backed by FileStore (virtual filesystem)
- Print capture
"""

from typing import Dict, Any, List
from core import FlowFile, TaskFactory, TaskError
from core.base_task import BaseTask


class ExecuteScriptTask(BaseTask):
    """Exécuter un script Python sur le contenu d'un FlowFile."""

    TYPE = "executeScript"
    VERSION = "2.0.0"
    NAME = "Execute Script"
    DESCRIPTION = "Exécuter un script Python sur le contenu d'un FlowFile"
    ICON = "terminal"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.script = self.config.get('script', '')
        self.script_engine = self.config.get('script_engine', 'python')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Exécuter le script sur le contenu du FlowFile."""
        from core.sandbox import build_sandbox_globals, make_sandbox_open

        try:
            created_files: list = []
            sandbox_open = make_sandbox_open(created_files=created_files)
            globals_dict, print_buf = build_sandbox_globals(
                sandbox_open=sandbox_open,
            )

            # Inject FlowFile context into namespace
            content = flowfile.get_content().decode('utf-8', errors='replace')
            attributes = dict(flowfile.get_attributes())
            local_ns = {
                'content': content,
                'attributes': attributes,
                'flowfile': flowfile,
                'flow_file': flowfile,  # alias for compat
            }

            # Inject filesystem service if configured
            fs_service_id = self.config.get('filesystem_service_id')
            if fs_service_id:
                fs_svc = self.get_service(fs_service_id)
                if fs_svc:
                    local_ns['fs'] = fs_svc

            exec(self.script, globals_dict, local_ns)

            if 'result' in local_ns:
                flowfile.set_content(str(local_ns['result']).encode('utf-8'))
            elif print_buf:
                flowfile.set_content(
                    "".join(print_buf).rstrip().encode('utf-8'))

            # Record created files as attributes
            if created_files:
                flowfile.set_attribute(
                    'script.created_files',
                    ', '.join(created_files),
                )

            return [flowfile]

        except ImportError as e:
            raise TaskError(f"Blocked by sandbox: {e}")
        except Exception as e:
            raise TaskError(f"Erreur lors de l'exécution du script: {str(e)}")

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'script': {
                'type': 'textarea',
                'required': True,
                'description': (
                    "Script Python à exécuter. Variables disponibles: "
                    "content (str), attributes (dict), flowfile (FlowFile). "
                    "Définir 'result' pour modifier le contenu du FlowFile. "
                    "open() écrit dans un sandbox FileStore. "
                    "Modules autorisés: json, re, csv, datetime, math, io, "
                    "requests, collections, itertools, hashlib, base64, etc."
                ),
            },
            'script_engine': {
                'type': 'select',
                'required': False,
                'description': 'Moteur de script',
                'options': ['python'],
                'default': 'python',
            },
            'filesystem_service_id': {
                'type': 'string', 'required': False,
                'description': 'Filesystem service ID for file access (fs.read_file(), fs.write_file(), etc.)',
            },
        }


TaskFactory.register(ExecuteScriptTask)
