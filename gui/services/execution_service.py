# Execution Service

"""
Service pour la gestion de l'exécution des flux.
Couche d'abstraction entre le GUI et ContinuousFlowExecutor.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, asdict

from engine.continuous_executor import ContinuousFlowExecutor
from engine.provenance import get_provenance_repository
from core import Flow, FlowFile
from gui.services.flow_service import FlowService

logger = logging.getLogger(__name__)


@dataclass
class ExecutionState:
    """État d'une exécution."""

    execution_id: str
    flow_id: str
    flow_name: str
    status: str  # pending, running, success, failed, cancelled
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    input_flowfiles: int = 0
    output_flowfiles: int = 0
    bytes_processed: int = 0
    duration_ms: float = 0.0
    errors: List[Dict[str, Any]] = None
    statistics: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convertir en dictionnaire JSON."""
        return {
            "execution_id": self.execution_id,
            "flow_id": self.flow_id,
            "flow_name": self.flow_name,
            "status": self.status,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "input_flowfiles": self.input_flowfiles,
            "output_flowfiles": self.output_flowfiles,
            "bytes_processed": self.bytes_processed,
            "duration_ms": self.duration_ms,
            "errors": self.errors or [],
            "statistics": self.statistics or {},
        }


class ExecutionService:
    """Service de gestion de l'exécution des flux."""

    def __init__(
        self,
        flow_service: Optional[FlowService] = None,
        max_workers: int = 10,
        max_retries: int = 3,
        timeout: int = 300,
    ):
        """
        Initialiser le service d'exécution.

        Args:
            flow_service: Service de gestion des flux
            max_workers: Nombre maximum de workers parallèles
            max_retries: Nombre maximum de retries par tâche
            timeout: Timeout en secondes
        """
        self.flow_service = flow_service or FlowService()
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.timeout = timeout
        self._active_executions: Dict[str, ExecutionState] = {}

    def execute_flow(
        self,
        flow: Flow,
        input_flowfiles: Optional[List[FlowFile]] = None,
        variables: Optional[Dict[str, Any]] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> ExecutionState:
        """
        Exécuter un flux.

        Args:
            flow: Flux à exécuter
            input_flowfiles: FlowFiles d'entrée optionnels
            variables: Variables runtime à injecter
            parameters: Override des flow.parameters

        Returns:
            ExecutionState avec les résultats
        """
        import uuid

        # Créer un ID d'exécution
        execution_id = str(uuid.uuid4())

        # Initialiser l'état
        state = ExecutionState(
            execution_id=execution_id,
            flow_id=flow.id,
            flow_name=flow.name,
            status="running",
            start_time=datetime.now(),
        )

        # Stocker l'état actif
        self._active_executions[execution_id] = state

        try:
            # Exécuter le flux via ContinuousFlowExecutor batch mode
            result = ContinuousFlowExecutor.run_batch(
                flow,
                input_flowfiles=input_flowfiles,
                parameters=parameters,
                max_workers=self.max_workers,
                max_retries=self.max_retries,
                timeout=float(self.timeout),
                provenance=get_provenance_repository(),
            )

            # Mettre à jour l'état
            state.status = "success" if result.success else "failed"
            state.end_time = datetime.now()
            state.duration_ms = result.duration_ms
            state.input_flowfiles = result.statistics.get("input_flowfiles", 0)
            state.output_flowfiles = result.statistics.get("output_flowfiles", 0)
            state.bytes_processed = result.statistics.get("bytes_processed", 0)
            state.statistics = result.statistics
            state.errors = result.errors

            logger.info(
                f"Exécution terminée: {execution_id} - "
                f"Succès: {result.success}, Durée: {result.duration_ms:.2f}ms"
            )

            return state

        except Exception as e:
            # Mettre à jour l'état en erreur
            state.status = "failed"
            state.end_time = datetime.now()
            state.errors = [{"error": str(e), "timestamp": datetime.now().isoformat()}]
            logger.error(f"Erreur d'exécution: {e}")
            return state

        finally:
            # Retirer de l'état actif après un certain délai
            # (peut être gardé pour l'historique)
            pass

    def execute_flow_from_id(
        self,
        flow_id: str,
        input_flowfiles: Optional[List[FlowFile]] = None,
        variables: Optional[Dict[str, Any]] = None,
    ) -> ExecutionState:
        """
        Exécuter un flux depuis son ID.

        Args:
            flow_id: ID du flux à exécuter
            input_flowfiles: FlowFiles d'entrée optionnels
            variables: Variables runtime à injecter

        Returns:
            ExecutionState avec les résultats
        """
        # Charger le flux
        flow = self.flow_service.load(flow_id)
        if flow is None:
            raise ValueError(f"Flux non trouvé: {flow_id}")

        return self.execute_flow(flow, input_flowfiles, variables)

    def execute_flow_from_file(
        self,
        filepath: str,
        input_flowfiles: Optional[List[FlowFile]] = None,
        variables: Optional[Dict[str, Any]] = None,
    ) -> ExecutionState:
        """
        Exécuter un flux depuis un fichier.

        Args:
            filepath: Chemin du fichier JSON
            input_flowfiles: FlowFiles d'entrée optionnels
            variables: Variables runtime à injecter

        Returns:
            ExecutionState avec les résultats
        """
        # Charger et parser le flux
        flow = self.flow_service.parse_from_file(filepath)
        return self.execute_flow(flow, input_flowfiles, variables)

    def create_input_flowfiles(self, content: bytes, attributes: Dict[str, str]) -> List[FlowFile]:
        """
        Créer des FlowFiles d'entrée.

        Args:
            content: Contenu des FlowFiles
            attributes: Attributs des FlowFiles

        Returns:
            Liste de FlowFiles
        """
        flowfile = FlowFile(content=content, attributes=attributes)
        return [flowfile]

    def create_input_file(self, filepath: str) -> List[FlowFile]:
        """
        Créer un FlowFile à partir d'un fichier.

        Args:
            filepath: Chemin du fichier

        Returns:
            Liste avec un FlowFile
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Fichier non trouvé: {filepath}")

        content = path.read_bytes()
        attributes = {
            "filename": path.name,
            "fileSize": str(path.stat().st_size),
        }

        return self.create_input_flowfiles(content, attributes)

    def get_active_executions(self) -> List[ExecutionState]:
        """
        Récupérer toutes les exécutions actives.

        Returns:
            Liste des exécutions actives
        """
        return list(self._active_executions.values())

    def get_execution(self, execution_id: str) -> Optional[ExecutionState]:
        """
        Récupérer une exécution par son ID.

        Args:
            execution_id: ID de l'exécution

        Returns:
            ExecutionState ou None
        """
        return self._active_executions.get(execution_id)

    def get_execution_history(self, limit: int = 100) -> List[ExecutionState]:
        """
        Récupérer l'historique des exécutions.

        Args:
            limit: Nombre maximum d'exécutions à retourner

        Returns:
            Liste des exécutions historiques
        """
        # Pour l'instant, on retourne toutes les exécutions
        # Une implémentation complète utiliserait un stockage persistant
        return list(self._active_executions.values())[-limit:]

    def cancel_execution(self, execution_id: str) -> bool:
        """
        Annuler une exécution.

        Args:
            execution_id: ID de l'exécution à annuler

        Returns:
            True si succès
        """
        state = self._active_executions.get(execution_id)
        if state and state.status == "running":
            state.status = "cancelled"
            state.end_time = datetime.now()
            logger.info(f"Exécution annulée: {execution_id}")
            return True
        return False

    def delete_execution(self, execution_id: str) -> bool:
        """Supprimer une exécution de l'historique."""
        if execution_id in self._active_executions:
            del self._active_executions[execution_id]
            logger.info(f"Exécution supprimée: {execution_id}")
            return True
        return False

    def clear_history(self):
        """Effacer l'historique des exécutions."""
        self._active_executions.clear()
        logger.info("Historique des exécutions effacé")

    def get_statistics(self) -> Dict[str, Any]:
        """
        Récupérer les statistiques d'exécution globales.

        Returns:
            Dictionnaire de statistiques
        """
        executions = list(self._active_executions.values())

        if not executions:
            return {
                "total_executions": 0,
                "success_count": 0,
                "failed_count": 0,
                "cancelled_count": 0,
                "avg_duration_ms": 0,
                "total_bytes_processed": 0,
            }

        success_count = sum(1 for e in executions if e.status == "success")
        failed_count = sum(1 for e in executions if e.status == "failed")
        cancelled_count = sum(1 for e in executions if e.status == "cancelled")

        durations = [e.duration_ms for e in executions if e.duration_ms > 0]
        avg_duration = sum(durations) / len(durations) if durations else 0

        total_bytes = sum(e.bytes_processed for e in executions)

        return {
            "total_executions": len(executions),
            "success_count": success_count,
            "failed_count": failed_count,
            "cancelled_count": cancelled_count,
            "avg_duration_ms": avg_duration,
            "total_bytes_processed": total_bytes,
            "success_rate": success_count / len(executions) * 100 if executions else 0,
        }