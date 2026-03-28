"""notebook_edit — Edit Jupyter notebook cells."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class NotebookEditHandler(BaseFsHandler):

    @property
    def name(self):
        return "notebook_edit"

    @property
    def description(self):
        return "Edit a Jupyter notebook cell (edit, insert, or delete)."

    @property
    def parameters_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Notebook file path (.ipynb)"},
                "cell_index": {"type": "integer", "description": "Cell index to edit"},
                "new_source": {"type": "string", "description": "New cell content"},
                "cell_type": {"type": "string", "description": "Cell type: code or markdown"},
                "operation": {"type": "string", "description": "edit, insert, or delete (default: edit)"},
                "filesystem": {"type": "string", "description": "Filesystem service name. Omit for default."},
            },
            "required": ["path"],
        }

    def execute(self, arguments: Dict[str, Any]) -> str:
        arguments = self._unwrap_json(arguments)
        arguments = self._resolve_expressions(arguments)
        path = arguments.get("path", "")
        if not path:
            return "Error: 'path' is required"
        fs = arguments.get("filesystem", "")

        _svc_name, path = self._parse_fs_url(path)
        if _svc_name:
            fs = _svc_name

        svc, workdir = self._resolve(fs)

        if svc == "filestore" or workdir:
            return "Error: notebook_edit requires a filesystem service with relay"

        if svc is None:
            return self._no_target_error(fs)

        try:
            cell_index = arguments.get("cell_index")
            new_source = arguments.get("new_source", "")
            cell_type = arguments.get("cell_type", "")
            operation = arguments.get("operation", "edit")
            result = svc._request("edit_notebook", path,
                                   cell_index=cell_index, new_source=new_source,
                                   cell_type=cell_type, operation=operation)
            op = result.get("operation", operation)
            idx = result.get("cell_index", cell_index)
            total = result.get("total_cells", "?")
            return f"Notebook {op}: cell {idx} ({total} cells total)"
        except Exception as e:
            return f"Error: {e}"
