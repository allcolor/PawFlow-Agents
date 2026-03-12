# Documentation Technique et Fonctionnelle - PyFi2

## Table des Matières

1. [Vue d'Ensemble](#1-vue-densemble)
2. [Architecture Logicielle](#2-architecture-logicielle)
3. [Concepts Fondamentaux](#3-concepts-fondamentaux)
4. [Format des Flux JSON](#4-format-des-flux-json)
5. [Interface Task](#5-interface-task)
6. [Interface Service](#6-interface-service)
7. [Interface Flow et Group](#7-interface-flow-et-group)
8. [FlowFile et Attributs](#8-flowfile-et-attributs)
9. [Système de Configuration](#9-système-de-configuration)
10. [Gestion des Variables Runtime](#10-gestion-des-variables-runtime)
11. [Référence Complète des Tâches](#11-référence-complète-des-tâches)
12. [Référence Complète des Services](#12-référence-complète-des-services)
13. [API du Moteur d'Exécution](#13-api-du-moteur-dexécution)
14. [GUI - Spécifications Techniques](#14-gui---spécifications-techniques)
15. [Sécurité et Authentification](#15-sécurité-et-authentification)
16. [Tests et Qualité](#16-tests-et-quality)
17. [Déploiement et Production](#17-déploiement-et-production)

---

## 1. Vue d'Ensemble

### 1.1. Objectif du Projet

PyFi2 est un framework Python de type Apache NiFi permettant de créer, déployer et monitorer des pipelines de données complexes. Il sépare clairement deux états :

- **État Création** : Design et édition des flux, services et tâches dans un dépôt (Git/DB/Fichier)
- **État Runtime** : Déploiement, configuration et exécution des flux avec gestion des variables

### 1.2. Principes de Conception

1. **Séparation des préoccupations** : Tâches, Services, Flux et Groupes sont modélisés indépendamment
2. **Configuration externalisée** : Les paramètres sont stockés dans le JSON, les overrides au runtime
3. **Extensibilité** : Nouveaux types de tâches/services ajoutables sans modification du core
4. **Flow-based programming** : Les données circulent via des FlowFiles entre les composants
5. **Déclaratif** : Les flux sont définis dans des fichiers JSON lisibles et éditables

### 1.3. Architecture Hiérarchique

```
Repository
├── Services (réutilisables)
├── Tasks (traitements unitaires)
├── Flows (orchestration de tâches)
│   └── Groups (regroupement logique)
└── Variables (overrides runtime)
```

---

## 2. Architecture Logicielle

### 2.1. Structure du Projet

```
pyfi2/
├── core/
│   ├── __init__.py
│   ├── interface_task.py        # Interface abstraite Task
│   ├── interface_service.py     # Interface abstraite Service
│   ├── interface_flow.py        # Interface abstraite Flow
│   ├── interface_group.py       # Interface abstraite Group
│   ├── flowfile.py              # Classe FlowFile et Attributes
│   ├── config_manager.py        # Gestion configuration (Git/DB/FS)
│   ├── variable_resolver.py     # Résolution des variables runtime
│   └── exceptions.py            # Exceptions personnalisées
│
├── tasks/
│   ├── __init__.py
│   ├── base_task.py             # Implémentation de base
│   ├── system/                  # Tâches système
│   │   ├── log_task.py
│   │   ├── replace_text_task.py
│   │   ├── wait_task.py
│   │   ├── notify_task.py
│   │   └── ...
│   ├── data/
│   │   ├── script_task.py
│   │   ├── shell_task.py
│   │   ├── convert_task.py
│   │   └── ...
│   ├── io/
│   │   ├── http_task.py
│   │   ├── sftp_task.py
│   │   ├── s3_task.py
│   │   ├── db_task.py
│   │   └── ...
│   └── control/
│       ├── flow_task.py         # Appeler un autre flow
│       ├── route_task.py
│       └── split_task.py
│
├── services/
│   ├── __init__.py
│   ├── base_service.py          # Implémentation de base
│   ├── auth/
│   │   ├── oauth2_authenticator.py
│   │   ├── oauth2_bearer_validator.py
│   │   └── ...
│   ├── connectivity/
│   │   ├── pulsar_connection.py
│   │   ├── db_connection.py
│   │   ├── sftp_connection.py
│   │   └── ...
│   └── utils/
│       ├── https_manager.py
│       └── ...
│
├── engine/
│   ├── __init__.py
│   ├── flow_parser.py           # Parser JSON de flux
│   ├── flow_validator.py        # Validation des flux
│   ├── executor.py              # Moteur d'exécution
│   ├── scheduler.py             # Scheduler des tâches
│   └── error_handler.py         # Gestion erreurs et retries
│
├── gui/
│   ├── __init__.py
│   ├── editor/                  # GUI de création
│   │   ├── app.py
│   │   ├── components/
│   │   │   ├── flow_canvas.py
│   │   │   ├── task_panel.py
│   │   │   └── property_editor.py
│   │   └── handlers/
│   │       ├── save_handler.py
│   │       └── import_export.py
│   └── runtime/                 # GUI de runtime
│       ├── app.py
│       ├── dashboard.py
│       ├── logs_viewer.py
│       └── metrics.py
│
├── config/
│   ├── __init__.py
│   ├── config.py                # Configuration globale
│   └── storage/
│       ├── storage_factory.py   # Factory pour Git/DB/FS
│       ├── git_storage.py
│       ├── sqlite_storage.py
│       └── filesystem_storage.py
│
├── tests/
├── examples/
├── docs/
└── main.py                      # Point d'entrée
```

### 2.2. Diagramme de Classes Principales

```
┌─────────────────────┐
│   Task (Interface)  │
├─────────────────────┤
│ - name: str         │
│ - version: str      │
│ - parameters: dict  │
│ + execute(flowfile) │
│ + get_schema()      │
└─────────────────────┘
         │
         │ hérite
         ▼
┌─────────────────────┐
│   BaseTask          │
├─────────────────────┤
│ - config: dict      │
│ + validate()        │
│ + cleanup()         │
└─────────────────────┘
         │
    ┌────┴────┬────────┬─────────┐
    │         │        │         │
    ▼         ▼        ▼         ▼
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│ Log   │ │ HTTP  │ │ Script│ │ Shell │
│Task   │ │Task   │ │Task   │ │Task   │
└───────┘ └───────┘ └───────┘ └───────┘

┌─────────────────────┐
│  Service (Interface)│
├─────────────────────┤
│ - name: str         │
│ - version: str      │
│ - parameters: dict  │
│ + connect()         │
│ + disconnect()      │
│ + get_schema()      │
└─────────────────────┘
         │
         │ hérite
         ▼
┌─────────────────────┐
│   BaseService       │
├─────────────────────┤
│ - instance: object  │
│ - pool_size: int    │
│ + init_connection() │
│ + health_check()    │
└─────────────────────┘
         │
    ┌────┴────┬────────┬─────────┐
    │         │        │         │
    ▼         ▼        ▼         ▼
┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│  OAuth│ │  DB   │ │ SFTP  │ │ Pulsar│
└───────┘ └───────┘ └───────┘ └───────┘

┌─────────────────────┐
│     FlowFile        │
├─────────────────────┤
│ - content: bytes    │
│ - attributes: dict  │
│ - process_id: str   │
│ + get_attr(key)     │
│ + set_attr(key, val)│
└─────────────────────┘

┌─────────────────────┐
│      Flow           │
├─────────────────────┤
│ - name: str         │
│ - entries: list     │  # Entrées (sources)
│ - exits: list       │  # Sorties (destinations)
│ - tasks: dict       │  # Mapping task_id -> TaskConfig
│ - relations: list   │  # Relations entre tâches
│ - parameters: dict  │  # Paramètres globaux
└─────────────────────┘
```

---

## 3. Concepts Fondamentaux

### 3.1. Les Quatre Types d'Objets

#### 3.1.1. Services
Les services sont des composants réutilisables qui fournissent des capacités spécifiques :
- **Authentification** : OAuth2, JWT, Basic Auth
- **Connectivité** : DB, SFTP, HTTP, Pulsar, S3
- **Utilitaires** : HTTPS Manager, Rate Limiter

**Caractéristiques** :
- Lifecycle indépendant (connect/disconnect)
- Peuvent être partagés entre plusieurs tâches
- Configuration persistante dans le repository

#### 3.1.2. Tâches
Les tâches sont des unités de traitement atomiques :
- **Transformation** : ReplaceText, Convert, Filter
- **IO** : HTTP, SFTP, DB, S3
- **Contrôle** : Wait, Notify, Split, Route
- **Custom** : Script Python, Shell command

**Caractéristiques** :
- Acceptent un FlowFile en entrée
- Produisent un ou plusieurs FlowFiles en sortie
- Exposent leurs paramètres via une interface standardisée

#### 3.1.3. Flux (Flow)
Un flux est une orchestration de tâches :
- **Entrées** : Sources de données (0 à N)
- **Sorties** : Destinations finales (0 à N)
- **Tâches** : Composants intermédiaires
- **Relations** : Connections entre tâches (avec routing)

**Caractéristiques** :
- Déclaratif (fichier JSON)
- Paramétrable et overrideable au runtime
- Peuvent appeler d'autres flux (composition)

#### 3.1.4. Groupes
Les groupes permettent d'organiser visuellement et logiquement :
- Regroupement de tâches/flux
- Arborescence hiérarchique
- Scope de configuration

### 3.2. Les FlowFiles

Un FlowFile représente une unité de données circulant dans le pipeline :

```python
class FlowFile:
    content: bytes              # Contenu binaire
    attributes: Dict[str, str]  # Métadonnées
    process_id: str             # UUID de l'instance
    
    # Méthodes utilitaires
    def get_attribute(key: str) -> Optional[str]
    def set_attribute(key: str, value: str)
    def delete_attribute(key: str)
    def get_content() -> bytes
    def write_content(data: bytes)
```

**Attributs standards** :
- `filename` : Nom du fichier original
- `fileSize` : Taille en octets
- `timestamp` : Timestamp d'entrée
- `uuid` : UUID unique
- `batch.id` : ID du batch
- `error.count` : Nombre d'erreurs

### 3.3. Les Relations

Une relation définit comment les FlowFiles circulent entre composants :

```json
{
  "from": "task_1",
  "to": "task_2",
  "relation_type": "success|failure|timeout|any",
  "routing_strategy": "direct|round_robin|load_balance",
  "queue_size": 1000
}
```

**Types de relations** :
- `success` : Tâche terminée avec succès
- `failure` : Tâche a échoué
- `timeout` : Tâche a timeout
- `any` : N'importe quel état

**Stratégies de routage** :
- `direct` : Envoi direct au destinataire
- `round_robin` : Distribution cyclique
- `load_balance` : Équilibrage de charge

---

## 4. Format des Flux JSON

### 4.1. Structure Générale

```json
{
  "$schema": "http://pyfi2.org/schemas/flow-v1.json",
  "metadata": {
    "name": "mon-flux",
    "version": "1.0.0",
    "description": "Description du flux",
    "author": "nom.prénom",
    "created": "2024-01-01T00:00:00Z",
    "modified": "2024-01-15T00:00:00Z"
  },
  "parameters": {
    "param1": "value1",
    "param2": "${variable_runtime}"
  },
  "entries": [
    {
      "id": "entry_1",
      "type": "http_source|file_source|db_source",
      "config": {}
    }
  ],
  "exits": [
    {
      "id": "exit_1",
      "type": "http_dest|file_dest|db_dest",
      "config": {}
    }
  ],
  "tasks": {
    "task_1": {
      "type": "replace_text",
      "name": "Remplacer texte",
      "parameters": {
        "search": "old",
        "replace": "new"
      }
    }
  },
  "groups": {
    "group_1": {
      "name": "Groupe de traitement",
      "tasks": ["task_1", "task_2"],
      "x": 100,
      "y": 100,
      "width": 400,
      "height": 200
    }
  },
  "relations": [
    {
      "id": "rel_1",
      "from": "entry_1",
      "to": "task_1",
      "type": "success",
      "routing": "direct"
    }
  ],
  "variables": {
    "variable_runtime": {
      "type": "string|secret|reference",
      "default": "default_value",
      "description": "Description",
      "required": true
    }
  }
}
```

### 4.2. Schema Complet

Voir fichier : `docs/schemas/flow-v1.json` (à créer)

### 4.3. Exemple Complexe

Voir : `examples/complex_flow.json`

---

## 5. Interface Task

### 5.1. Définition de l'Interface

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from core.flowfile import FlowFile

class Task(ABC):
    """Interface abstraite pour toutes les tâches."""
    
    # Métadonnées de la tâche (class attributes)
    TYPE: str                    # Type unique (ex: "log", "http")
    VERSION: str                 # Version de l'implémentation
    NAME: str                    # Nom affiché
    DESCRIPTION: str             # Description détaillée
    ICON: str                    # Icône pour l'UI
    
    @abstractmethod
    def __init__(self, config: Dict[str, Any]):
        """
        Initialiser la tâche avec sa configuration.
        
        Args:
            config: Dictionnaire des paramètres de la tâche
        """
        pass
    
    @abstractmethod
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """
        Exécuter la tâche sur un FlowFile.
        
        Args:
            flowfile: FlowFile d'entrée
            
        Returns:
            Liste de FlowFiles de sortie (1 ou plusieurs)
        """
        pass
    
    @abstractmethod
    def get_parameter_schema(self) -> Dict[str, Any]:
        """
        Retourner le schéma des paramètres pour l'UI.
        
        Returns:
            Schema décrivant chaque paramètre (type, validation, etc.)
        """
        pass
    
    def validate(self) -> List[str]:
        """
        Valider la configuration de la tâche.
        
        Returns:
            Liste de messages d'erreur (vide si valide)
        """
        pass
    
    def initialize(self):
        """
        Initialiser la tâche (appelé avant l'exécution).
        """
        pass
    
    def cleanup(self):
        """
        Nettoyage de la tâche (appelé après exécution).
        """
        pass
```

### 5.2. Implémentation de Base

```python
class BaseTask(Task):
    """Implémentation de base avec fonctionnalités communes."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.parameters = self._parse_parameters(config)
        self._validate_config()
    
    def _parse_parameters(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Résoudre les variables dans les paramètres."""
        resolved = {}
        for key, value in config.items():
            if isinstance(value, str) and value.startswith('${'):
                # Résolution de variable
                resolved[key] = VariableResolver.resolve(value)
            else:
                resolved[key] = value
        return resolved
    
    def _validate_config(self):
        """Valider la configuration."""
        errors = []
        schema = self.get_parameter_schema()
        for param_name, param_schema in schema.items():
            if param_schema.get('required', False):
                if param_name not in self.parameters:
                    errors.append(f"Paramètre requis manquant : {param_name}")
        if errors:
            raise ValueError("; ".join(errors))
```

### 5.3. Exemple d'Implémentation

```python
class LogTask(BaseTask):
    TYPE = "log"
    NAME = "Log"
    DESCRIPTION = "Logguer un message"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.message = self.parameters.get('message', '')
        self.level = self.parameters.get('level', 'INFO')
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        import logging
        logger = logging.getLogger(self.__class__.__name__)
        
        # Logguer le message
        msg = self.message.format(
            **{k: flowfile.get_attribute(k) for k in flowfile.attributes}
        )
        
        if self.level == 'DEBUG':
            logger.debug(msg)
        elif self.level == 'INFO':
            logger.info(msg)
        # ... autres niveaux
        
        # Retourner le FlowFile inchangé
        return [flowfile]
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'message': {
                'type': 'string',
                'required': True,
                'description': 'Message à logguer',
                'placeholder': 'Message: ${filename}'
            },
            'level': {
                'type': 'select',
                'options': ['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                'default': 'INFO'
            }
        }
```

### 5.4. Catalogue Complet des Tâches

Voir section 11 pour la liste complète avec schémas de paramètres.

---

## 6. Interface Service

### 6.1. Définition de l'Interface

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class Service(ABC):
    """Interface abstraite pour tous les services."""
    
    # Métadonnées (class attributes)
    TYPE: str                    # Type unique
    VERSION: str                 # Version
    NAME: str                    # Nom affiché
    DESCRIPTION: str             # Description
    
    @abstractmethod
    def __init__(self, config: Dict[str, Any]):
        """Initialiser le service."""
        pass
    
    @abstractmethod
    def connect(self):
        """Établir la connexion au service."""
        pass
    
    @abstractmethod
    def disconnect(self):
        """Fermer la connexion."""
        pass
    
    @abstractmethod
    def get_parameter_schema(self) -> Dict[str, Any]:
        """Schema des paramètres."""
        pass
    
    def validate(self) -> List[str]:
        """Valider la configuration."""
        pass
    
    def health_check(self) -> bool:
        """Vérifier l'état de santé du service."""
        pass
    
    def get_instance(self):
        """Retourner l'instance connectée (pour utilisation par les tâches)."""
        pass
```

### 6.2. Gestion du Lifecycle

```python
class BaseService(Service):
    """Implémentation de base avec gestion du lifecycle."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.parameters = self._resolve_variables(config)
        self._connection = None
        self._validated = False
    
    def connect(self):
        """Établir la connexion avec gestion d'erreurs."""
        if self._connection is not None:
            return
        
        try:
            self._connection = self._create_connection()
            self._validated = True
        except Exception as e:
            raise ServiceConnectionError(f"Échec connexion : {e}")
    
    def disconnect(self):
        """Fermer la connexion proprement."""
        if self._connection is not None:
            try:
                self._close_connection()
            finally:
                self._connection = None
    
    def __enter__(self):
        """Support du contexte contextuel."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Fermer la connexion après utilisation."""
        self.disconnect()
    
    @abstractmethod
    def _create_connection(self):
        """Créer la connexion réelle (implémenté par sous-classe)."""
        pass
    
    @abstractmethod
    def _close_connection(self):
        """Fermer la connexion réelle."""
        pass
```

### 6.3. Exemple de Service

```python
class SFTPService(BaseService):
    TYPE = "sftp_connection"
    NAME = "Connexion SFTP"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.host = self.parameters['host']
        self.port = self.parameters.get('port', 22)
        self.username = self.parameters['username']
        self.password = self.parameters.get('password')
        self.key_file = self.parameters.get('key_file')
    
    def _create_connection(self):
        import paramiko
        
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        if self.key_file:
            key = paramiko.RSAKey.from_private_key_file(self.key_file)
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                key_filename=self.key_file
            )
        else:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password
            )
        
        return client
    
    def _close_connection(self):
        if self._connection:
            self._connection.close()
    
    def get_connection(self):
        """Retourner le client SFTP."""
        return self._connection
    
    def get_parameter_schema(self):
        return {
            'host': {
                'type': 'string',
                'required': True,
                'description': 'Hôte SFTP'
            },
            'port': {
                'type': 'integer',
                'default': 22,
                'min': 1,
                'max': 65535
            },
            'username': {
                'type': 'string',
                'required': True
            },
            'password': {
                'type': 'password',
                'required': False
            },
            'key_file': {
                'type': 'file',
                'required': False
            }
        }
```

---

## 7. Interface Flow et Group

### 7.1. Interface Flow

```python
from typing import Dict, List, Optional
from core.flowfile import FlowFile
from core.task import Task
from core.service import Service

class Flow:
    """Orchestration de tâches."""
    
    def __init__(self, config: Dict[str, Any]):
        self.name = config['name']
        self.version = config['version']
        self.description = config.get('description', '')
        self.parameters = config.get('parameters', {})
        self.variables = config.get('variables', {})
        
        # Entrées et sorties
        self.entries = self._parse_entries(config.get('entries', []))
        self.exits = self._parse_exits(config.get('exits', []))
        
        # Tâches
        self.tasks = self._parse_tasks(config.get('tasks', {}))
        
        # Groupes
        self.groups = self._parse_groups(config.get('groups', {}))
        
        # Relations
        self.relations = self._parse_relations(config.get('relations', []))
    
    def _parse_entries(self, entries_config: List[Dict]) -> List[Dict]:
        """Parser les entrées."""
        return entries_config
    
    def _parse_exits(self, exits_config: List[Dict]) -> List[Dict]:
        """Parser les sorties."""
        return exits_config
    
    def _parse_tasks(self, tasks_config: Dict) -> Dict[str, Task]:
        """Parser et instancier les tâches."""
        tasks = {}
        for task_id, task_config in tasks_config.items():
            task_class = TaskFactory.get(task_config['type'])
            task = task_class(task_config.get('parameters', {}))
            tasks[task_id] = task
        return tasks
    
    def _parse_groups(self, groups_config: Dict) -> Dict[str, Dict]:
        """Parser les groupes."""
        return groups_config
    
    def _parse_relations(self, relations_config: List[Dict]) -> List[Dict]:
        """Parser les relations."""
        return relations_config
    
    def execute(self, input_flowfile: Optional[FlowFile] = None) -> List[FlowFile]:
        """
        Exécuter le flow.
        
        Args:
            input_flowfile: FlowFile optionnel pour les entrées
            
        Returns:
            Liste des FlowFiles de sortie
        """
        # Créer les FlowFiles d'entrée
        flowfiles = self._create_input_flowfiles(input_flowfile)
        
        # Exécuter le DAG
        output_flowfiles = self._execute_dag(flowfiles)
        
        return output_flowfiles
    
    def _create_input_flowfiles(self, input_flowfile: Optional[FlowFile]) -> List[FlowFile]:
        """Créer les FlowFiles initiaux."""
        flowfiles = []
        for entry in self.entries:
            ff = FlowFile(
                content=self._read_entry(entry),
                attributes=self._get_entry_attributes(entry)
            )
            flowfiles.append(ff)
        return flowfiles
    
    def _execute_dag(self, flowfiles: List[FlowFile]) -> List[FlowFile]:
        """Exécuter le DAG des tâches."""
        # Topological sort des tâches
        sorted_tasks = self._topological_sort()
        
        # Execution
        current_flowfiles = flowfiles
        for task_id in sorted_tasks:
            task = self.tasks[task_id]
            new_flowfiles = []
            for ff in current_flowfiles:
                outputs = task.execute(ff)
                new_flowfiles.extend(outputs)
            current_flowfiles = new_flowfiles
        
        return current_flowfiles
    
    def _topological_sort(self) -> List[str]:
        """Tri topologique des tâches."""
        # Implémentation de l'algorithme de tri topologique
        pass
    
    def get_statistics(self) -> Dict[str, Any]:
        """Retourner les statistiques du flow."""
        return {
            'name': self.name,
            'total_tasks': len(self.tasks),
            'total_relations': len(self.relations),
            'entry_count': len(self.entries),
            'exit_count': len(self.exits)
        }
```

### 7.2. Interface Group

```python
class Group:
    """Regroupement logique de tâches."""
    
    def __init__(self, config: Dict[str, Any]):
        self.id = config['id']
        self.name = config.get('name', '')
        self.description = config.get('description', '')
        self.tasks = config.get('tasks', [])
        self.flows = config.get('flows', [])
        
        # Position et dimension pour l'UI
        self.x = config.get('x', 0)
        self.y = config.get('y', 0)
        self.width = config.get('width', 400)
        self.height = config.get('height', 200)
    
    def add_task(self, task_id: str):
        """Ajouter une tâche au groupe."""
        if task_id not in self.tasks:
            self.tasks.append(task_id)
    
    def remove_task(self, task_id: str):
        """Retirer une tâche du groupe."""
        if task_id in self.tasks:
            self.tasks.remove(task_id)
    
    def get_children(self) -> List[str]:
        """Retourner tous les enfants (tâches + sous-groupes)."""
        return self.tasks.copy()
```

---

## 8. FlowFile et Attributs

### 8.1. Classe FlowFile

```python
import uuid
from typing import Dict, Optional, BinaryIO
from datetime import datetime

class FlowFile:
    """Représente une unité de données dans le pipeline."""
    
    def __init__(
        self,
        content: bytes = b'',
        attributes: Optional[Dict[str, str]] = None,
        process_id: Optional[str] = None
    ):
        self.content = content
        self.attributes = attributes or {}
        self.process_id = process_id or str(uuid.uuid4())
        self._original_content = content.copy()
    
    # --- Accès aux attributs ---
    
    def get_attribute(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Récupérer un attribut."""
        return self.attributes.get(key, default)
    
    def set_attribute(self, key: str, value: str):
        """Définir un attribut."""
        self.attributes[key] = str(value)
    
    def delete_attribute(self, key: str):
        """Supprimer un attribut."""
        if key in self.attributes:
            del self.attributes[key]
    
    def get_attributes(self) -> Dict[str, str]:
        """Récupérer tous les attributs."""
        return self.attributes.copy()
    
    def set_attributes(self, attributes: Dict[str, str]):
        """Définir tous les attributs."""
        self.attributes = attributes.copy()
    
    # --- Gestion du contenu ---
    
    def get_content(self) -> bytes:
        """Récupérer le contenu."""
        return self.content
    
    def set_content(self, content: bytes):
        """Définir le contenu."""
        self.content = content
    
    def write_content(self, file_obj: BinaryIO):
        """Écrire le contenu depuis un fichier."""
        self.content = file_obj.read()
    
    def read_content(self) -> BinaryIO:
        """Lire le contenu comme fichier."""
        from io import BytesIO
        return BytesIO(self.content)
    
    def clone(self) -> 'FlowFile':
        """Créer une copie."""
        return FlowFile(
            content=self.content.copy(),
            attributes=self.attributes.copy(),
            process_id=str(uuid.uuid4())
        )
    
    # --- Méthodes utilitaires ---
    
    def size(self) -> int:
        """Taille du contenu."""
        return len(self.content)
    
    def is_empty(self) -> bool:
        """Vérifier si vide."""
        return len(self.content) == 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertir en dictionnaire (pour sérialisation)."""
        return {
            'process_id': self.process_id,
            'size': len(self.content),
            'attributes': self.attributes
        }
    
    def __repr__(self):
        return f"FlowFile(process_id={self.process_id}, size={len(self.content)})"
```

### 8.2. Attributs Standards

```python
STANDARD_ATTRIBUTES = {
    # Métadonnées de base
    'filename': 'Nom du fichier original',
    'fileSize': 'Taille en octets',
    'timestamp': 'Timestamp d\'entrée (ISO8601)',
    'uuid': 'UUID unique du FlowFile',
    
    # Contrôle de flux
    'batch.id': 'ID du batch',
    'process.id': 'ID du processus',
    'route.key': 'Clé de routage',
    
    # Erreurs
    'error.message': 'Message d\'erreur',
    'error.count': 'Nombre d\'erreurs',
    'retry.count': 'Nombre de tentatives',
    
    # Données
    'mime.type': 'Type MIME',
    'encoding': 'Encodage',
    'line.count': 'Nombre de lignes',
    
    # Système
    'pyfi2.task.id': 'ID de la tâche en cours',
    'pyfi2.flow.id': 'ID du flow',
    'pyfi2.execution.id': 'ID de l\'exécution'
}
```

---

## 9. Système de Configuration

### 9.1. Config Manager

```python
from enum import Enum
from typing import Optional, Dict, Any
from abc import ABC, abstractmethod

class StorageType(Enum):
    FILESYSTEM = "filesystem"
    GIT = "git"
    SQLITE = "sqlite"
    POSTGRES = "postgres"

class ConfigStorage(ABC):
    """Interface abstraite pour le stockage."""
    
    @abstractmethod
    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder un flux."""
        pass
    
    @abstractmethod
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        """Charger un flux."""
        pass
    
    @abstractmethod
    def delete_flow(self, flow_id: str) -> bool:
        """Supprimer un flux."""
        pass
    
    @abstractmethod
    def list_flows(self) -> List[str]:
        """Lister tous les flux."""
        pass
    
    @abstractmethod
    def save_task(self, task_type: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder une tâche custom."""
        pass
    
    @abstractmethod
    def load_service(self, service_type: str, config: Dict[str, Any]) -> bool:
        """Sauvegarder un service."""
        pass

class ConfigManager:
    """Manager principal de configuration."""
    
    def __init__(self, storage_type: StorageType, config: Dict[str, Any]):
        self.storage_type = storage_type
        self.storage = self._create_storage(storage_type, config)
    
    def _create_storage(self, storage_type: StorageType, config: Dict[str, Any]):
        """Factory pour créer le bon storage."""
        if storage_type == StorageType.FILESYSTEM:
            from config.storage.filesystem_storage import FilesystemStorage
            return FilesystemStorage(config)
        elif storage_type == StorageType.GIT:
            from config.storage.git_storage import GitStorage
            return GitStorage(config)
        elif storage_type == StorageType.SQLITE:
            from config.storage.sqlite_storage import SqliteStorage
            return SqliteStorage(config)
        # ...
    
    def save_flow(self, flow_id: str, config: Dict[str, Any]) -> bool:
        return self.storage.save_flow(flow_id, config)
    
    def load_flow(self, flow_id: str) -> Optional[Dict[str, Any]]:
        return self.storage.load_flow(flow_id)
    
    def delete_flow(self, flow_id: str) -> bool:
        return self.storage.delete_flow(flow_id)
    
    def list_flows(self) -> List[str]:
        return self.storage.list_flows()
```

### 9.2. Fichier de Configuration Global

```python
# config/config.py

from dataclasses import dataclass
from typing import Dict, Any
from enum import Enum

class StorageType(Enum):
    FILESYSTEM = "filesystem"
    GIT = "git"
    SQLITE = "sqlite"
    POSTGRES = "postgres"

@dataclass
class Config:
    """Configuration globale de l'application."""
    
    # Stockage
    storage_type: StorageType = StorageType.FILESYSTEM
    storage_config: Dict[str, Any] = None
    
    # Paths
    flows_path: str = "./flows"
    tasks_path: str = "./tasks"
    services_path: str = "./services"
    logs_path: str = "./logs"
    
    # Runtime
    max_workers: int = 10
    max_retries: int = 3
    retry_delay: int = 5
    timeout: int = 300
    
    # GUI
    gui_host: str = "0.0.0.0"
    gui_port: int = 8501
    
    # Variables globales
    global_variables: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.storage_config is None:
            self.storage_config = {}
        if self.global_variables is None:
            self.global_variables = {}
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Config':
        """Créer Config depuis un dictionnaire."""
        return cls(**data)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertir en dictionnaire."""
        return {
            'storage_type': self.storage_type.value,
            'storage_config': self.storage_config,
            'flows_path': self.flows_path,
            'tasks_path': self.tasks_path,
            'services_path': self.services_path,
            'logs_path': self.logs_path,
            'max_workers': self.max_workers,
            'max_retries': self.max_retries,
            'retry_delay': self.retry_delay,
            'timeout': self.timeout,
            'gui_host': self.gui_host,
            'gui_port': self.gui_port,
            'global_variables': self.global_variables
        }
```

---

## 10. Gestion des Variables Runtime

### 10.1. Système de Variables

```python
from typing import Dict, Any, Optional
from jinja2 import Template

class VariableType(Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    SECRET = "secret"
    REFERENCE = "reference"
    JSON = "json"

class Variable:
    """Représentation d'une variable."""
    
    def __init__(
        self,
        name: str,
        var_type: VariableType,
        default: Any = None,
        description: str = "",
        required: bool = False,
        scope: str = "flow"  # flow, task, global
    ):
        self.name = name
        self.var_type = var_type
        self.default = default
        self.description = description
        self.required = required
        self.scope = scope
        self.value: Optional[Any] = None
    
    def resolve(self, context: Dict[str, Any]) -> Any:
        """Résoudre la valeur dans un contexte."""
        # 1. Chercher dans le contexte
        if self.name in context:
            self.value = context[self.name]
        # 2. Utiliser la valeur par défaut
        elif self.default is not None:
            self.value = self.default
        # 3. Lever erreur si requis
        elif self.required:
            raise ValueError(f"Variable requise non définie : {self.name}")
        # 4. None si non requis
        else:
            self.value = None
        
        return self.value
    
    def validate(self, value: Any) -> List[str]:
        """Valider une valeur pour la variable."""
        errors = []
        
        if value is None:
            if self.required:
                errors.append(f"Variable requise : {self.name}")
            return errors
        
        # Validation par type
        if self.var_type == VariableType.INTEGER:
            if not isinstance(value, int):
                errors.append(f"Type attendu : integer, reçu : {type(value)}")
        elif self.var_type == VariableType.FLOAT:
            if not isinstance(value, (int, float)):
                errors.append(f"Type attendu : float, reçu : {type(value)}")
        # ... autres types
        
        return errors

class VariableResolver:
    """Résolveur de variables dans les paramètres."""
    
    _variables: Dict[str, Variable] = {}
    _context: Dict[str, Any] = {}
    
    @classmethod
    def register_variables(cls, variables: Dict[str, Variable]):
        """Enregistrer les variables d'un flow."""
        cls._variables.update(variables)
    
    @classmethod
    def set_context(cls, context: Dict[str, Any]):
        """Définir le contexte de résolution."""
        cls._context = context
    
    @classmethod
    def resolve(cls, value: str) -> Any:
        """
        Résoudre une chaîne contenant des variables.
        
        Exemple: "Hello ${name}!" -> "Hello John!"
        """
        if not isinstance(value, str) or '${' not in value:
            return value
        
        template = Template(value)
        return template.render(cls._context)
    
    @classmethod
    def resolve_all(cls, config: Dict[str, Any]) -> Dict[str, Any]:
        """Résoudre toutes les variables dans une configuration."""
        resolved = {}
        for key, value in config.items():
            if isinstance(value, str):
                resolved[key] = cls.resolve(value)
            elif isinstance(value, dict):
                resolved[key] = cls.resolve_all(value)
            elif isinstance(value, list):
                resolved[key] = [
                    cls.resolve(v) if isinstance(v, str) else v
                    for v in value
                ]
            else:
                resolved[key] = value
        return resolved
```

### 10.2. Override des Paramètres

```python
class ParameterOverride:
    """Permet de override les paramètres au runtime."""
    
    def __init__(self, flow_id: str):
        self.flow_id = flow_id
        self.overrides: Dict[str, Dict[str, Any]] = {}
    
    def set_task_parameter(self, task_id: str, param_name: str, value: Any):
        """Override un paramètre de tâche."""
        if task_id not in self.overrides:
            self.overrides[task_id] = {}
        self.overrides[task_id][param_name] = value
    
    def set_flow_parameter(self, param_name: str, value: Any):
        """Override un paramètre de flow."""
        if 'flow' not in self.overrides:
            self.overrides['flow'] = {}
        self.overrides['flow'][param_name] = value
    
    def apply(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Appliquer les overrides à une configuration."""
        import copy
        resolved = copy.deepcopy(config)
        
        # Appliquer les overrides de flow
        if 'flow' in self.overrides:
            for key, value in self.overrides['flow'].items():
                resolved[key] = value
        
        # Appliquer les overrides de tâches
        if 'tasks' in resolved and 'tasks' in self.overrides:
            for task_id, task_config in resolved['tasks'].items():
                if task_id in self.overrides['tasks']:
                    for key, value in self.overrides['tasks'][task_id].items():
                        if isinstance(task_config, dict) and key in task_config:
                            task_config[key] = value
        
        return resolved
```

---

*(Le document continue dans les fichiers suivants...)*