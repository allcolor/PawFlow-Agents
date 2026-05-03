# Wait Task Implementation

"""
Task Wait - Wait for a duration before continuing.
"""

import time
from typing import Dict, Any, List
from core import FlowFile
from core.base_task import BaseTask


class WaitTask(BaseTask):
    """
    Task for waiting a duration before continuing.
    
    Useful for introducing pauses in the flow or waiting
    until a condition is met.
    """
    
    TYPE = "wait"
    VERSION = "1.0.0"
    NAME = "Attendre"
    DESCRIPTION = "Wait for a duration before continuing"
    ICON = "wait"
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the Wait task.
        
        Args:
            config: Configuration avec:
                - duration: Duration in milliseconds (requis)
                - duration_unit: Time unit (default: MS)
        """
        super().__init__(config)
        
        self.duration = self.config.get('duration', 0)
        self.duration_unit = self.config.get('duration_unit', 'MS').upper()
        
        # Convert to seconds
        self.duration_seconds = self._convert_to_seconds()
    
    def _convert_to_seconds(self) -> float:
        """Convert the duration to seconds."""
        if self.duration_unit == 'MS':
            return self.duration / 1000.0
        elif self.duration_unit == 'SEC':
            return float(self.duration)
        elif self.duration_unit == 'MIN':
            return self.duration * 60
        elif self.duration_unit == 'HOUR':
            return self.duration * 3600
        else:
            raise ValueError(f"Time unit invalide: {self.duration_unit}")
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Execute the Wait task.
        
        Args:
            flowfile: Input FlowFile
            
        Returns:
            List containing the unchanged FlowFile
        """
        # Wait for the specified duration
        time.sleep(self.duration_seconds)
        
        return [flowfile]
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Return the parameter schema.
        
        Returns:
            Parameter schema for the UI
        """
        return {
            'duration': {
                'type': 'integer',
                'required': True,
                'description': 'Durée d\'attente',
                'min': 0
            },
            'duration_unit': {
                'type': 'select',
                'required': False,
                'description': 'Time unit',
                'options': ['MS', 'SEC', 'MIN', 'HOUR'],
                'default': 'MS'
            }
        }