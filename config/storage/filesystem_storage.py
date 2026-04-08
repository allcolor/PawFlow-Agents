# Filesystem Storage

"""
Implémentation du stockage sur le système de fichiers.
"""

import os
import json
import shutil
from typing import Dict, Any, Optional, List
from datetime import datetime


class FilesystemStorage:
    """Stockage sur le système de fichiers."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialiser le stockage filesystem.
        
        Args:
            config: Configuration avec:
                - flows_path: chemin vers les fichiers de flux
        """
        self.flows_path = config.get('flows_path', './flows')
        self.tasks_path = config.get('tasks_path', './tasks')
        self.services_path = config.get('services_path', './services')
        
        # Créer les répertoires s'ils n'existent pas
        self._ensure_directories()
    
    def _ensure_directories(self):
        """Créer les répertoires s'ils n'existent pas."""
        os.makedirs(self.flows_path, exist_ok=True)
        os.makedirs(self.tasks_path, exist_ok=True)
        os.makedirs(self.services_path, exist_ok=True)
    
    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """
        Sauvegarder un flux.
        
        Args:
            flow_id: ID du flux
            config: Configuration du flux
            
        Returns:
            True si succès
        """
        try:
            # Ajouter les métadonnées
            if 'modified_at' not in config:
                config['modified_at'] = datetime.now().isoformat()
            
            # Sauvegarder dans un fichier JSON
            filepath = os.path.join(self.flows_path, f"{flow_id}.json")
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            return True
        
        except Exception as e:
            print(f"Erreur lors de la sauvegarde du flux {flow_id}: {e}")
            return False
    
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """
        Charger un flux.
        
        Args:
            flow_id: ID du flux
            
        Returns:
            Configuration du flux ou None si non trouvé
        """
        try:
            filepath = os.path.join(self.flows_path, f"{flow_id}.json")
            
            if not os.path.exists(filepath):
                return None
            
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        except Exception as e:
            print(f"Erreur lors du chargement du flux {flow_id}: {e}")
            return None
    
    def delete_flow(self, flow_id: str) -> bool:
        """
        Supprimer un flux.
        
        Args:
            flow_id: ID du flux
            
        Returns:
            True si succès
        """
        try:
            filepath = os.path.join(self.flows_path, f"{flow_id}.json")
            
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
            
            return False
        
        except Exception as e:
            print(f"Erreur lors de la suppression du flux {flow_id}: {e}")
            return False
    
    def list_flows(self) -> List[str]:
        """
        Lister tous les flux.
        
        Returns:
            Liste des IDs de flux
        """
        try:
            flow_ids = []
            
            for filename in os.listdir(self.flows_path):
                if filename.endswith('.json'):
                    flow_id = filename[:-5]  # Retirer .json
                    flow_ids.append(flow_id)
            
            return sorted(flow_ids)
        
        except Exception as e:
            print(f"Erreur lors de la liste des flux: {e}")
            return []
    
    def save_task(self, task_type: str, config: Dict[str, Any]) -> bool:
        """
        Sauvegarder une tâche custom.
        
        Args:
            task_type: Type de la tâche
            config: Configuration de la tâche
            
        Returns:
            True si succès
        """
        try:
            task_dir = os.path.join(self.tasks_path, task_type)
            os.makedirs(task_dir, exist_ok=True)
            
            filepath = os.path.join(task_dir, f"{config.get('id', 'default')}.json")
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            return True
        
        except Exception as e:
            print(f"Erreur lors de la sauvegarde de la tâche {task_type}: {e}")
            return False
    
    def load_service(self, service_type: str, config: Dict[str, Any]) -> bool:
        """
        Sauvegarder un service.
        
        Args:
            service_type: Type du service
            config: Configuration du service
            
        Returns:
            True si succès
        """
        try:
            service_dir = os.path.join(self.services_path, service_type)
            os.makedirs(service_dir, exist_ok=True)
            
            filepath = os.path.join(service_dir, f"{config.get('id', 'default')}.json")
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            return True
        
        except Exception as e:
            print(f"Erreur lors de la sauvegarde du service {service_type}: {e}")
            return False