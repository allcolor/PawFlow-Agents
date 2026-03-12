# Control Tasks

"""
Modules Control pour PyFi2.
Tâches pour le contrôle de flux.
"""

from tasks.control.split_content import SplitContentTask
from tasks.control.merge_content import MergeContentTask
from tasks.control.route_on_attribute import RouteOnAttributeTask
from tasks.control.duplicate_content import DuplicateContentTask
from tasks.control.execute_flow import ExecuteFlowTask
from tasks.control.ports import InputPortTask, OutputPortTask
from tasks.control.funnel import FunnelTask
from tasks.control.control_rate import ControlRateTask
from tasks.control.wait_notify import WaitTask, NotifyTask

__all__ = [
    'SplitContentTask', 'MergeContentTask', 'RouteOnAttributeTask',
    'DuplicateContentTask', 'ExecuteFlowTask',
    'InputPortTask', 'OutputPortTask', 'FunnelTask',
    'ControlRateTask', 'WaitTask', 'NotifyTask',
]