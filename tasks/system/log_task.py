# Log Task Implementation

"""
Tâche Log - Logguer un message avec formatage.
"""

import logging
from typing import Dict, Any, List
from core import FlowFile
from core.base_task import BaseTask
from core.expression import resolve_expression


class LogTask(BaseTask):
    """
    Tâche pour logguer un message.
    
    Permet de logger des messages avec différents niveaux de log
    et d'inclure les attributs du FlowFile.
    """
    
    TYPE = "log"
    VERSION = "1.0.0"
    NAME = "Log"
    DESCRIPTION = "Logguer un message avec formatage"
    ICON = "log"
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialiser la tâche Log.
        
        Args:
            config: Configuration avec:
                - message: Message à logguer (requis)
                - level: Niveau de log (par défaut: INFO)
                - logger_name: Nom du logger (optionnel)
                - include_attributes: Inclure les attributs (par défaut: false)
        """
        super().__init__(config)
        
        self.message = self.config.get('message', '')
        self.level = self.config.get('level', 'INFO').upper()
        self.logger_name = self.config.get('logger_name')
        self.include_attributes = self.config.get('include_attributes', False)
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Exécuter la tâche Log.
        
        Args:
            flowfile: FlowFile d'entrée
            
        Returns:
            Liste avec le FlowFile inchangé
        """
        # Construire le message
        log_message = self._format_message(self.message, flowfile)
        
        # Logguer
        self._log_with_level(log_message)
        
        # Retourner le FlowFile inchangé
        return [flowfile]
    
    def _format_message(self, message: str, flowfile: FlowFile) -> str:
        """
        Formater le message avec les attributs du FlowFile.
        Utilise le moteur d'expression generique pour resoudre ${...}.
        """
        # Enrichir les attributs avec des valeurs par defaut pour les champs standard
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
        Logguer un message avec le niveau spécifié.
        
        Args:
            message: Message à logguer
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
        Retourner le schéma des paramètres.
        
        Returns:
            Schema des paramètres pour l'UI
        """
        return {
            'message': {
                'type': 'string',
                'required': True,
                'description': 'Message à logguer (supporte les placeholders)',
                'placeholder': 'Message: ${filename}, taille: ${fileSize}',
                'help': 'Les placeholders disponibles: ${filename}, ${fileSize}, ${uuid}, ${timestamp}, ${mime.type}, ${line.count}, ${process.id}'
            },
            'level': {
                'type': 'select',
                'required': False,
                'description': 'Niveau de log',
                'options': ['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                'default': 'INFO'
            },
            'logger_name': {
                'type': 'string',
                'required': False,
                'description': 'Nom du logger (optionnel, utilise le nom de la tâche par défaut)',
                'placeholder': 'mon_logger'
            },
            'include_attributes': {
                'type': 'boolean',
                'required': False,
                'description': 'Inclure les attributs du FlowFile dans le log',
                'default': False
            }
        }