# SQLite Storage

"""
SQLite storage implementation.
"""

import sqlite3
import json
from typing import Dict, Any, Optional, List
from datetime import datetime


class SqliteStorage:
    """SQLite storage."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize SQLite storage.
        
        Args:
            config: Configuration with:
                - database: path to the database
        """
        self.database = config.get('database', './pawflow.db')
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _init_database(self):
        """Initialize the database."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Flows table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS flows (
                id TEXT PRIMARY KEY,
                config TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tasks table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                config TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Services table
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
        """Save a flow."""
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
            print(f"Error saving flow {flow_id}: {e}")
            return False
    
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Load a flow."""
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
            print(f"Error loading flow {flow_id}: {e}")
            return None
    
    def delete_flow(self, flow_id: str) -> bool:
        """Delete a flow."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('DELETE FROM flows WHERE id = ?', (flow_id,))
            conn.commit()
            conn.close()
            
            return True
        
        except Exception as e:
            print(f"Error deleting flow {flow_id}: {e}")
            return False
    
    def list_flows(self) -> List[str]:
        """List all flows."""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute('SELECT id FROM flows ORDER BY id')
            rows = cursor.fetchall()
            conn.close()
            
            return [row['id'] for row in rows]
        
        except Exception as e:
            print(f"Error listing flows: {e}")
            return []
    
    def save_task(self, task_type: str, config: Dict[str, Any]) -> bool:
        """Save a custom task."""
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
            print(f"Error saving task {task_type}: {e}")
            return False
    
    def load_service(self, service_type: str, config: Dict[str, Any]) -> bool:
        """Save a service."""
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
            print(f"Error saving service {service_type}: {e}")
            return False
