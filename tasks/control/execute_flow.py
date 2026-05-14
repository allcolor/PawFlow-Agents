# ExecuteFlow Task

"""
Task ExecuteFlow - Execute a subflow and pass FlowFiles through it.
"""

from typing import Dict, Any, List
from core import FlowFile, TaskError, TaskFactory
from core.base_task import BaseTask
from engine import FlowParser
from engine.continuous_executor import ContinuousFlowExecutor


# Recursion guard: store the stack of sub-flow paths on the FlowFile
# itself. This works across threads (the executor dispatches tasks on
# a worker pool) because the attribute travels with the data.
_STACK_ATTR = "_subflow_stack"
MAX_SUBFLOW_DEPTH = 10


class ExecuteFlowTask(BaseTask):
    """Execute an external flow and pass FlowFiles through it.

    Supports propagation of parent flow parameters to the subflow
    through an explicit mapping (parameter_mapping) ou par propagation directe.
    """

    TYPE = "executeFlow"
    VERSION = "1.1.0"
    NAME = "Execute Flow"
    DESCRIPTION = "Execute a JSON flow and pass FlowFiles through it"
    ICON = "flow"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.flow_path = self.config.get('flow_path', '')
        self.pass_attributes = self.config.get('pass_attributes', True)
        self.parameter_mapping = self.config.get('parameter_mapping', {})
        self.port_mapping = self.config.get('port_mapping', {})
        self._runtime_context: Dict[str, str] = {}

    def set_runtime_context(self, *, user_id: str = "", conversation_id: str = "",
                            scope: str = "", agent_name: str = "") -> None:
        self._runtime_context = {
            "user_id": user_id or "",
            "conversation_id": conversation_id or "",
            "scope": scope or "",
            "agent_name": agent_name or "",
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Execute the subflow with the FlowFile as input."""
        if not self.flow_path:
            raise TaskError("The 'flow_path' parameter is required.")

        import os
        if not os.path.exists(self.flow_path):
            raise TaskError(f"Le fichier de flow n'existe pas: {self.flow_path}")

        # Recursion guard: read the stack of sub-flow paths currently
        # active from the FlowFile attribute (propagates across threads
        # because it travels with the data).
        _key = os.path.abspath(self.flow_path)
        _stack_raw = flowfile.get_attribute(_STACK_ATTR) or ""
        _stack = [p for p in _stack_raw.split("|") if p]
        if _key in _stack:
            raise TaskError(
                f"Sub-flow recursion detected: '{self.flow_path}' is "
                f"already on the call stack {_stack}.")
        if len(_stack) >= MAX_SUBFLOW_DEPTH:
            raise TaskError(
                f"Sub-flow depth limit ({MAX_SUBFLOW_DEPTH}) reached; "
                f"stack={_stack}")
        _stack.append(_key)
        flowfile.set_attribute(_STACK_ATTR, "|".join(_stack))
        try:
            # Parse and execute the subflow
            flow = FlowParser.parse_from_file(self.flow_path)

            # If port_mapping specifies an input port, tag the FlowFile so the
            # subflow executor can route it to the correct InputPort task.
            input_port_id = self._get_input_port_id()
            if input_port_id:
                flowfile.set_attribute('_target_input_port', input_port_id)
                self._reorder_root_tasks(flow, input_port_id)

            # Build the subflow's ParameterContext
            child_ctx = self._build_child_parameter_context(flow)
            child_params = child_ctx._params if child_ctx else {}

            result = ContinuousFlowExecutor.run_batch(
                flow,
                input_flowfiles=[flowfile],
                parameters=child_params if child_params else None,
                runtime_context=self._runtime_context or None,
            )

            if not result.success:
                errors_str = "; ".join(str(e) for e in result.errors)
                raise TaskError(f"Exécution du sous-flow échouée: {errors_str}")

            # Copy parent attributes if requested
            if self.pass_attributes:
                parent_attrs = flowfile.get_attributes()
                for ff in result.output_flowfiles:
                    for key, value in parent_attrs.items():
                        if not ff.get_attribute(key):
                            ff.set_attribute(key, value)

            # Apply output port mapping: set route.relationship based on which
            # OutputPort the FlowFile came from
            self._apply_output_port_mapping(result.output_flowfiles)

            return result.output_flowfiles

        except TaskError:
            raise
        except Exception as e:  # noqa: BLE001 — kept broad for legacy compat
            raise TaskError(f"Erreur lors de l'exécution du sous-flow: {e}")
        finally:
            # Restore the stack on the flowfile (removes our entry so
            # siblings downstream don't inherit it).
            _stack_after = [p for p in _stack if p != _key]
            if _stack_after:
                flowfile.set_attribute(_STACK_ATTR, "|".join(_stack_after))
            elif flowfile.get_attribute(_STACK_ATTR):
                flowfile.set_attribute(_STACK_ATTR, "")

    def _get_input_port_id(self) -> str:
        """Return the target input port task ID from port_mapping, or empty string."""
        if not self.port_mapping:
            return ""
        return self.port_mapping.get("input", {}).get("port_task_id", "")

    def _reorder_root_tasks(self, flow, target_port_id: str):
        """Reorder the flow's tasks dict so the target input port is first.

        The FlowExecutor sends input FlowFiles to the first root task.
        By placing the target InputPort first, the FlowFile is routed correctly.
        """
        if target_port_id not in flow.tasks:
            return
        # Rebuild tasks OrderedDict with target first
        from collections import OrderedDict
        new_tasks = OrderedDict()
        new_tasks[target_port_id] = flow.tasks[target_port_id]
        for tid, task in flow.tasks.items():
            if tid != target_port_id:
                new_tasks[tid] = task
        flow.tasks = new_tasks

    def _apply_output_port_mapping(self, output_flowfiles: List[FlowFile]):
        """Set route.relationship on output FlowFiles based on output port mapping.

        Each FlowFile that passed through an OutputPort has a 'port.name' attribute
        set to the OutputPortTask's configured port_name.
        The port_mapping.output maps port_task_id -> relationship_name.
        We resolve port_task_id to port_name by reading the subflow definition.
        """
        if not self.port_mapping:
            return
        output_mapping = self.port_mapping.get("output", {})
        if not output_mapping:
            return

        # Build port_name -> relationship lookup by reading the subflow JSON
        import os
        port_name_to_rel: Dict[str, str] = {}
        try:
            import json as _json
            with open(self.flow_path, "r", encoding="utf-8") as f:
                subflow_data = _json.load(f)
            for port_id, relationship in output_mapping.items():
                task_def = subflow_data.get("tasks", {}).get(port_id, {})
                port_name = task_def.get("parameters", {}).get("port_name", port_id)
                port_name_to_rel[port_name] = relationship
                # Also map by port_id itself as fallback
                port_name_to_rel[port_id] = relationship
        except Exception:
            # Fallback: use port_id directly as the port_name key
            port_name_to_rel = dict(output_mapping)

        for ff in output_flowfiles:
            port_name = ff.get_attribute('port.name')
            if port_name and port_name in port_name_to_rel:
                ff.set_attribute('route.relationship', port_name_to_rel[port_name])

    def _build_child_parameter_context(self, subflow):
        """Build the ParameterContext for the subflow.

        Strategy:
        1. If parameter_mapping is defined, use it to create a mapped context
        2. Otherwise, propagate the parent's ParameterContext directly
        3. Subflow's own flow.parameters serve as defaults (overridden by mapping)
        4. Validate that all required subflow params are provided
        """
        from core.parameter_context import ParameterContext

        # Start with subflow's own defaults
        child_ctx = ParameterContext(subflow.parameters)

        if self.parameter_mapping and self._parameter_context:
            # Map parent params → subflow params via the mapping
            mapped_ctx = self._parameter_context.with_mapping(self.parameter_mapping)
            # Merge: mapping overrides subflow defaults
            child_ctx = child_ctx.with_overrides(mapped_ctx.parameters)
        elif self._parameter_context:
            # No mapping — propagate parent context directly
            # (subflow defaults + parent params as override)
            child_ctx = child_ctx.with_overrides(self._parameter_context.parameters)

        # Validate: check for unresolved ${flow.parameters.X} in subflow task configs
        self._validate_subflow_params(subflow, child_ctx)

        return child_ctx

    def _validate_subflow_params(self, subflow, ctx):
        """Warn about subflow parameters that remain unresolved.

        Checks each task config in the subflow for ${flow.parameters.X}
        references that the child_ctx cannot resolve.
        """
        import re
        missing = set()
        for task_id, task_config in self._get_subflow_task_configs(subflow):
            for key, value in task_config.items():
                if isinstance(value, str) and '${flow.parameters.' in value:
                    resolved = ctx.resolve(value)
                    # Check if any ${flow.parameters.X} remain unresolved
                    remaining = re.findall(r'\$\{flow\.parameters\.([^}]+)\}', resolved)
                    missing.update(remaining)

        if missing:
            import logging
            logging.getLogger(__name__).warning(
                f"Subflow '{subflow.name}' has unresolved parameters: {sorted(missing)}. "
                f"Consider adding them to parameter_mapping."
            )

    def _get_subflow_task_configs(self, subflow):
        """Extract task configs from a parsed subflow."""
        for task_id, task in subflow.tasks.items():
            config = task._original_config if hasattr(task, '_original_config') else task.config
            yield task_id, config

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'flow_path': {
                'type': 'string', 'required': True,
                'description': "Path to the flow JSON file to execute",
            },
            'pass_attributes': {
                'type': 'boolean', 'required': False, 'default': True,
                'description': "Passer les attributs du FlowFile parent au sous-flow",
            },
            'parameter_mapping': {
                'type': 'object', 'required': False, 'default': {},
                'description': "Mapping of parent parameters -> subflow parameters. Ex: {\"sub_env\": \"${flow.parameters.env}\"}",
            },
            'port_mapping': {
                'type': 'object', 'required': False, 'default': {},
                'description': "Subflow port mapping. input.port_task_id = target input port, output.{port_id} = relationship name",
            },
        }


# Register in the factory
TaskFactory.register(ExecuteFlowTask)
