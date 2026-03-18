# Base Task Implementation

"""
Implémentation de base pour toutes les tâches.
Fournit des fonctionnalités communes et la structure standard.
"""

from typing import Dict, Any, List, Optional
from abc import ABC
from core import Task, TaskError, FlowFile
from core.variable_resolver import VariableResolverMixin
from core.bulletin import BulletinBoard
import json


class BaseTask(VariableResolverMixin, Task, ABC):
    """
    Implémentation de base pour toutes les tâches.

    Gère la validation, la résolution de variables et les fonctions utilitaires.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialiser la tâche avec sa configuration.

        Args:
            config: Configuration de la tâche
        """
        # Sauvegarder la configuration originale (avant toute résolution)
        self._original_config = config.copy()

        # Résoudre les variables dans la configuration
        resolved_config = self._resolve_variables(config)

        # Appeler l'initialisation de la sous-classe
        super().__init__(resolved_config)

        # Stocker la configuration résolue
        self.config = resolved_config

        # Controller services (injected by the executor)
        self._services: Dict[str, Any] = {}

        # Parameter context (injected by the executor at runtime)
        self._parameter_context = None

    def log(self, level: str = "INFO", message: str = "", **kwargs):
        """
        Logguer un message.
        
        Args:
            level: Niveau de log (DEBUG, INFO, WARNING, ERROR)
            message: Message à logguer
            **kwargs: Attributs à inclure dans le log
        """
        import logging
        logger = logging.getLogger(self.__class__.__name__)
        
        log_message = message
        if kwargs:
            log_message += f" | Attributes: {json.dumps(kwargs)}"
        
        if level == "DEBUG":
            logger.debug(log_message)
        elif level == "INFO":
            logger.info(log_message)
        elif level == "WARNING":
            logger.warning(log_message)
        elif level == "ERROR":
            logger.error(log_message)
    
    def get_attribute(self, flowfile: FlowFile, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Récupérer un attribut du FlowFile avec résolution de variable.
        
        Args:
            flowfile: FlowFile source
            key: Clé de l'attribut
            default: Valeur par défaut
            
        Returns:
            Valeur de l'attribut ou valeur par défaut
        """
        value = flowfile.get_attribute(key, default)
        
        if value and '${' in value:
            # Résoudre les variables dans l'attribut
            return self._resolve_string(value)
        
        return value
    
    def set_attribute(self, flowfile: FlowFile, key: str, value: str):
        """
        Définir un attribut sur le FlowFile.
        
        Args:
            flowfile: FlowFile cible
            key: Clé de l'attribut
            value: Valeur de l'attribut
        """
        resolved_value = self._resolve_string(str(value))
        flowfile.set_attribute(key, resolved_value)
    
    def create_flowfile(
        self,
        content: bytes,
        attributes: Optional[Dict[str, str]] = None,
        parent_flowfile: Optional[FlowFile] = None
    ) -> FlowFile:
        """
        Créer un nouveau FlowFile.
        
        Args:
            content: Contenu du FlowFile
            attributes: Attributs optionnels
            parent_flowfile: FlowFile parent pour hériter des attributs
            
        Returns:
            Nouveau FlowFile
        """
        new_attributes = attributes.copy() if attributes else {}
        
        # Hériter des attributs du parent si spécifié
        if parent_flowfile:
            for key, value in parent_flowfile.get_attributes().items():
                if key not in new_attributes:
                    new_attributes[key] = value
        
        return FlowFile(
            content=content,
            attributes=new_attributes
        )
    
    def read_content(self, flowfile: FlowFile) -> bytes:
        """
        Lire le contenu d'un FlowFile.
        
        Args:
            flowfile: FlowFile source
            
        Returns:
            Contenu binaire
        """
        return flowfile.get_content()
    
    def write_content(self, flowfile: FlowFile, content: bytes):
        """
        Écrire le contenu dans un FlowFile.
        
        Args:
            flowfile: FlowFile cible
            content: Contenu à écrire
        """
        flowfile.set_content(content)
    
    def split_content(self, content: bytes, split_by: bytes = b"\n") -> List[bytes]:
        """
        Découper du contenu en plusieurs parties.
        
        Args:
            content: Contenu à découper
            split_by: Caractère de séparation (bytes)
            
        Returns:
            Liste de contenus découpés
        """
        if isinstance(content, str):
            content = content.encode('utf-8')
        
        parts = content.split(split_by)
        return [p for p in parts if p]  # Filtrer les parties vides
    
    def merge_content(self, contents: List[bytes], separator: bytes = b"\n") -> bytes:
        """
        Fusionner plusieurs contenus.
        
        Args:
            contents: Listes de contenus
            separator: Séparateur entre contenus (bytes)
            
        Returns:
            Contenu fusionné
        """
        return separator.join(contents)
    
    def validate_json(self, content: str) -> bool:
        """
        Valider un contenu JSON.
        
        Args:
            content: Contenu JSON à valider
            
        Returns:
            True si valide
        """
        try:
            json.loads(content)
            return True
        except json.JSONDecodeError:
            return False
    
    def parse_json(self, content: str) -> Dict[str, Any]:
        """
        Parser un contenu JSON.
        
        Args:
            content: Contenu JSON
            
        Returns:
            Objet JSON parseé
            
        Raises:
            TaskError: Si le JSON est invalide
        """
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise TaskError(f"JSON invalide: {e}")
    
    def serialize_json(self, data: Any) -> str:
        """
        Sérialiser un objet en JSON.
        
        Args:
            data: Objet à sérialiser
            
        Returns:
            Chaîne JSON
            
        Raises:
            TaskError: Si la sérialisation échoue
        """
        try:
            return json.dumps(data, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as e:
            raise TaskError(f"Erreur de sérialisation JSON: {e}")
    
    def copy_flowfile_attributes(self, source: FlowFile, target: FlowFile, exclude: Optional[List[str]] = None):
        """
        Copier les attributs d'un FlowFile à un autre.
        
        Args:
            source: FlowFile source
            target: FlowFile cible
            exclude: Liste d'attributs à exclure
        """
        exclude = exclude or []
        
        for key, value in source.get_attributes().items():
            if key not in exclude:
                target.set_attribute(key, value)
    
    def get_service(self, service_id: str) -> Optional[Any]:
        """Get a controller service by ID.

        Services are injected by the executor from Flow.services.
        Tasks reference services by the ID defined in the flow JSON.

        Args:
            service_id: The service identifier

        Returns:
            The service instance, or None if not found
        """
        svc = self._services.get(service_id)
        if svc is None:
            # Also try resolving from config (e.g. "service_id": "my_cache")
            config_svc_id = self.config.get("service_id", "")
            if config_svc_id and config_svc_id in self._services:
                return self._services[config_svc_id]
        return svc

    def set_services(self, services: Dict[str, Any]):
        """Inject controller services (called by executor)."""
        self._services = services

    def set_parameter_context(self, ctx):
        """Inject the flow's ParameterContext (called by executor).

        Once injected, ${flow.parameters.X} in the original config are
        re-resolved with actual values, and resolve_value() becomes available.
        """
        self._parameter_context = ctx
        # Re-resolve config from the original (unresolved) config + parameter context
        if ctx:
            self.config = ctx.resolve_config(self._original_config)

    @property
    def parameter_context(self):
        """Access the flow's ParameterContext (may be None if not injected)."""
        return self._parameter_context

    def resolve_value(self, value: str, flowfile: Optional[FlowFile] = None) -> str:
        """Resolve a string at runtime using both flow parameters and FlowFile attributes.

        Resolution order:
        1. ${flow.parameters.X} → from ParameterContext
        2. ${attr} → from FlowFile attributes
        3. ${env.VAR} → from environment
        4. Unresolved → left as-is

        Args:
            value: String potentially containing ${...} expressions
            flowfile: Optional FlowFile for attribute resolution

        Returns:
            Resolved string
        """
        if not isinstance(value, str) or '${' not in value:
            return value
        from core.expression import resolve_expression
        params = self._parameter_context.parameters if self._parameter_context else {}
        attrs = flowfile.get_attributes() if flowfile else {}
        return resolve_expression(value, attributes=attrs, parameters=params)

    def bulletin(self, level: str, message: str):
        """Post a message to the bulletin board."""
        BulletinBoard.get_instance().post(level, self.__class__.__name__, message)

    def initialize(self):
        """Called once after services are injected and connected.

        Override in tasks that need setup before scheduling begins
        (e.g. registering HTTP routes, opening sockets).
        """
        pass

    def reset(self):
        """Reset internal state. Called when queues are cleared.

        Override in stateful tasks (e.g. mergeContent) to clear
        internal buffers/bins. Also used to re-arm one-shot tasks
        like generateFlowFile.
        """
        pass

    def prioritize(self, flowfile) -> int:
        """Return priority for a FlowFile entering this task's input queue.

        Higher number = more urgent. Override for custom rules.
        Default: read from 'priority' attribute or 0.

        Convention: 0=normal, 5=elevated, 10=urgent, -5=low/batch.
        """
        val = flowfile.get_attribute("priority")
        try:
            return int(val) if val else 0
        except (ValueError, TypeError):
            return 0

    def has_pending_input(self) -> bool:
        """Whether this task has self-generated input ready.

        Override in self-triggering tasks (e.g. httpReceiver) to return True
        when the task has data to produce without needing an incoming connection.
        The continuous executor scheduler checks this for root tasks.
        """
        return False

    @property
    def is_persistent_source(self) -> bool:
        """Whether this task is a persistent/recurring source (listener, poller).

        Override to return True in tasks that listen for external events
        (HTTP receiver, Telegram receiver, etc.). A flow with only non-persistent
        sources will auto-stop when all queues are empty and no workers are active.
        """
        return False

    def get_task_id(self) -> str:
        """
        Retourner l'ID de la tâche.
        
        Returns:
            ID de la tâche (basé sur le nom de classe)
        """
        # Cette méthode sera remplie par le flux parent
        return self.__class__.__name__