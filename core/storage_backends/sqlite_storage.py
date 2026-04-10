# SQLite Storage

"""
Implémentation du stockage SQLite.
"""

import sqlite3
import json
from typing import Dict, Any, Optional, List
from datetime import datetime


class SqliteStorage:
    """Stockage SQLite."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialiser le stockage SQLite.
        
        Args:
            config: Configuration avec:
                - database: chemin vers la base de données
        """
        self.database = config.get('database', './pawflow.db')
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Obtenir une connexion à la base de données."""
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_database(self):
        """Initialiser la base de données."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Table des flux
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS flows (
                id TEXT PRIMARY KEY,
                config TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Table des tâches
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                config TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Table des services
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_type TEXT NOT NULL,
                config TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder un flux."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            config_json = json.dumps(config, ensure_ascii=False)
            
            cursor.execute('''
                INSERT OR REPLACE INTO flows (id, config, modified_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (flow_id, config_json))
            
            conn.commit()
            conn.close()
            
            return True
        
        except Exception as e:
            print(f"Erreur lors de la sauvegarde du flux {flow_id}: {e}")
            return False
    
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Charger un flux."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('SELECT config FROM flows WHERE id = ?', (flow_id,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                return json.loads(row['config'])
            
            return None
        
        except Exception as e:
            print(f"Erreur lors du chargement du flux {flow_id}: {e}")
            return None
    
    def delete_flow(self, flow_id: str) -> bool:
        """Supprimer un flux."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('DELETE FROM flows WHERE id = ?', (flow_id,))
            conn.commit()
            conn.close()
            
            return True
        
        except Exception as e:
            print(f"Erreur lors de la suppression du flux {flow_id}: {e}")
            return False
    
    def list_flows(self) -> List[str]:
        """Lister tous les flux."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('SELECT id FROM flows ORDER BY id')
            rows = cursor.fetchall()
            conn.close()
            
            return [row['id'] for row in rows]
        
        except Exception as e:
            print(f"Erreur lors de la liste des flux: {e}")
            return []
    
    def save_task(self, task_type: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder une tâche custom."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            config_json = json.dumps(config, ensure_ascii=False)
            
            cursor.execute('''
                INSERT INTO tasks (task_type, config)
                VALUES (?, ?)
            ''', (task_type, config_json))
            
            conn.commit()
            conn.close()
            
            return True
        
        except Exception as e:
            print(f"Erreur lors de la sauvegarde de la tâche {task_type}: {e}")
            return False
    
    def load_service(self, service_type: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder un service."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            config_json = json.dumps(config, ensure_ascii=False)
            
            cursor.execute('''
                INSERT INTO services (service_type, config)
                VALUES (?, ?)
            ''', (service_type, config_json))
            
            conn.commit()
            conn.close()
            
            return True
        
        except Exception as e:
            print(f"Erreur lors de la sauvegarde du service {service_type}: {e}")
            return False