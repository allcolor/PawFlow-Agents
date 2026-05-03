# Filesystem Storage

"""
Filesystem storage implementation.
"""

import os
import json
import shutil
from typing import Dict, Any, Optional, List
from datetime import datetime


class FilesystemStorage:
    """Filesystem storage."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize filesystem storage.
        
        Args:
            config: Configuration with:
                - flows_path: path to flow files
        """
        self.flows_path = config.get('flows_path', './flows')
        self.tasks_path = config.get('tasks_path', './tasks')
        self.services_path = config.get('services_path', './services')
        
        # Create directories if they don't exist
        self._ensure_directories()
    
    def _ensure_directories(self):
        """Create directories if they don't exist."""
        os.makedirs(self.flows_path, exist_ok=True)
        os.makedirs(self.tasks_path, exist_ok=True)
        os.makedirs(self.services_path, exist_ok=True)
    
    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """
        Save a flow.
        
        Args:
            flow_id: Flow ID
            config: Flow configuration
            
        Returns:
            True if successful
        """
        try:
            # Add metadata
            if 'modified_at' not in config:
                config['modified_at'] = datetime.now().isoformat()
            
            # Save to a JSON file
            filepath = os.path.join(self.flows_path, f"{flow_id}.json")
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            return True
        
        except Exception as e:
            print(f"Error saving flow {flow_id}: {e}")
            return False
    
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """
        Load a flow.
        
        Args:
            flow_id: Flow ID
            
        Returns:
            Flow configuration or None if not found
        """
        try:
            filepath = os.path.join(self.flows_path, f"{flow_id}.json")
            
            if not os.path.exists(filepath):
                return None
            
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        
        except Exception as e:
            print(f"Error loading flow {flow_id}: {e}")
            return None
    
    def delete_flow(self, flow_id: str) -> bool:
        """
        Delete a flow.
        
        Args:
            flow_id: Flow ID
            
        Returns:
            True if successful
        """
        try:
            filepath = os.path.join(self.flows_path, f"{flow_id}.json")
            
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
            
            return False
        
        except Exception as e:
            print(f"Error deleting flow {flow_id}: {e}")
            return False
    
    def list_flows(self) -> List[str]:
        """
        List all flows.
        
        Returns:
            List of flow IDs
        """
        try:
            flow_ids = []
            
            for filename in os.listdir(self.flows_path):
                if filename.endswith('.json'):
                    flow_id = filename[:-5]  # Remove .json
                    flow_ids.append(flow_id)
            
            return sorted(flow_ids)
        
        except Exception as e:
            print(f"Error listing flows: {e}")
            return []
    
    def save_task(self, task_type: str, config: Dict[str, Any]) -> bool:
        """
        Save a custom task.
        
        Args:
            task_type: Task type
            config: Task configuration
            
        Returns:
            True if successful
        """
        try:
            task_dir = os.path.join(self.tasks_path, task_type)
            os.makedirs(task_dir, exist_ok=True)
            
            filepath = os.path.join(task_dir, f"{config.get('id', 'default')}.json")
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            return True
        
        except Exception as e:
            print(f"Error saving task {task_type}: {e}")
            return False
    
    def load_service(self, service_type: str, config: Dict[str, Any]) -> bool:
        """
        Save a service.
        
        Args:
            service_type: Service type
            config: Service configuration
            
        Returns:
            True if successful
        """
        try:
            service_dir = os.path.join(self.services_path, service_type)
            os.makedirs(service_dir, exist_ok=True)
            
            filepath = os.path.join(service_dir, f"{config.get('id', 'default')}.json")
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            
            return True
        
        except Exception as e:
            print(f"Error saving service {service_type}: {e}")
            return False
