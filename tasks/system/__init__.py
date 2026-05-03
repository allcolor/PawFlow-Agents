# Tasks System Module

"""
System tasks module.
Tasks de base pour le fonctionnement du framework.
"""

from typing import List
from core import FlowFile, TaskFactory
from core.base_task import BaseTask


def register_system_tasks():
    """Register all system tasks."""
    from tasks.system.log_task import LogTask
    from tasks.system.replace_text_task import ReplaceTextTask
    from tasks.system.wait_task import WaitTask
    from tasks.system.fail_task import FailTask
    from tasks.system.update_attribute import UpdateAttributeTask
    from tasks.system.generate_flowfile import GenerateFlowFileTask
    from tasks.system.cron_trigger import CronTriggerTask

    # Register tasks
    TaskFactory.register(LogTask)
    TaskFactory.register(ReplaceTextTask)
    TaskFactory.register(WaitTask)
    TaskFactory.register(FailTask)
    TaskFactory.register(UpdateAttributeTask)
    TaskFactory.register(GenerateFlowFileTask)
    TaskFactory.register(CronTriggerTask)