# Log Task Implementation

"""
Task Log - Log a formatted message.
"""

import logging
from typing import Dict, Any, List
from core import FlowFile
from core.base_task import BaseTask
from core.expression import resolve_expression


class LogTask(BaseTask):
    """
    Task for logging a message.
    
    Allows logging messages with different log levels
    and including FlowFile attributes.
    """
    
    TYPE = "log"
    VERSION = "1.0.0"
    NAME = "Log"
    DESCRIPTION = "Log a formatted message"
    ICON = "log"
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the Log task.
        
        Args:
            config: Configuration avec:
                - message: Message to log (required)
                - level: Log level (default: INFO)
                - logger_name: Nom du logger (optionnel)
                - include_attributes: Include attributes (default: false)
        """
        super().__init__(config)
        
        self.message = self.config.get('message', '')
        self.level = self.config.get('level', 'INFO').upper()
        self.logger_name = self.config.get('logger_name')
        self.include_attributes = self.config.get('include_attributes', False)
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Execute the Log task.
        
        Args:
            flowfile: Input FlowFile
            
        Returns:
            List containing the unchanged FlowFile
        """
        # Construire le message
        log_message = self._format_message(self.message, flowfile)
        
        # Logguer
        self._log_with_level(log_message)
        
        # Return the FlowFile unchanged
        return [flowfile]
    
    def _format_message(self, message: str, flowfile: FlowFile) -> str:
        """
        Format the message with FlowFile attributes.
        Uses the generic expression engine to resolve ${...}.
        """
        # Enrich attributes with default values for standard fields
        attrs = flowfile.get_attributes()
        attrs.setdefault('fileSize', str(flowfile.size()))
        attrs.setdefault('uuid', flowfile.process_id[:8])
        attrs.setdefault('process.id', flowfile.process_id[:8])
        # Make FlowFile body available as ${content}
        if 'content' not in attrs:
            try:
                attrs['content'] = flowfile.get_content().decode('utf-8', errors='replace')[:10000]
            except Exception:
                attrs['content'] = ''

        formatted = resolve_expression(message, parameters=attrs)

        if self.include_attributes:
            formatted += f"\nAttributes: {flowfile.get_attributes()}"

        return formatted
    
    def _log_with_level(self, message: str):
        """
        Log a message with the specified level.
        
        Args:
            message: Message to log
        """
        logger = logging.getLogger(self.logger_name or self.__class__.__name__)
        
        if self.level == 'DEBUG':
            logger.debug(message)
        elif self.level == 'INFO':
            logger.info(message)
        elif self.level == 'WARNING':
            logger.warning(message)
        elif self.level == 'ERROR':
            logger.error(message)
        else:
            logger.info(message)  # Default to INFO
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Return the parameter schema.
        
        Returns:
            Parameter schema for the UI
        """
        return {
            'message': {
                'type': 'string',
                'required': True,
                'description': 'Message to log (supports placeholders)',
                'placeholder': 'Message: ${filename}, taille: ${fileSize}',
                'help': 'Les placeholders disponibles: ${filename}, ${fileSize}, ${uuid}, ${timestamp}, ${mime.type}, ${line.count}, ${process.id}'
            },
            'level': {
                'type': 'select',
                'required': False,
                'description': 'Log level',
                'options': ['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                'default': 'INFO'
            },
            'logger_name': {
                'type': 'string',
                'required': False,
                'description': 'Logger name (optional, uses the default task name)',
                'placeholder': 'mon_logger'
            },
            'include_attributes': {
                'type': 'boolean',
                'required': False,
                'description': 'Include FlowFile attributes in the log',
                'default': False
            }
        }