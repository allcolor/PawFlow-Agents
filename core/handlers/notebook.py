"""notebook_edit — Edit Jupyter notebook cells."""

from typing import Any, Dict
from core.handlers._fs_base import BaseFsHandler


class NotebookEditHandler(BaseFsHandler):

    @property
    def name(self):
        return "notebook_edit"

    @property
    def description(self):
        return (
            "Edit a Jupyter notebook (.ipynb) cell by index.\n\n"
            "Three operations are supported:\n"
            "  edit   -- replace the content of an existing cell (default).\n"
            "  insert -- insert a new cell BEFORE the given cell_index.\n"
            "  delete -- remove the cell at cell_index.\n\n"
            "Cell indices are 0-based (first cell = 0). The path must be an\n"
            "absolute path to the .ipynb file on the target filesystem service.\n\n"
            "Parameters:\n"
            "  path       -- absolute path to the notebook file.\n"
            "  cell_index -- 0-based index of the target cell.\n"
            "  new_source -- the replacement or new cell content (required for\n"
            "                edit and insert; ignored for delete).\n"
            "  cell_type  -- 'code' or 'markdown' (used by insert; edit preserves\n"
            "                the existing type if omitted).\n"
            "  operation  -- 'edit' (default), 'insert', or 'delete'.\n"
            "  filesystem -- filesystem service name; omit for the default service.\n\n"
            "This tool executes on the relay, so the notebook must be accessible\n"
            "from the connected filesystem service."
        )

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
                                   cell_type=cell_type, operation=operation,
                                   local=bool(arguments.get("local", False)))
            op = result.get("operation", operation)
            idx = result.get("cell_index", cell_index)
            total = result.get("total_cells", "?")
            return f"Notebook {op}: cell {idx} ({total} cells total)"
        except Exception as e:
            return f"Error: {e}"
