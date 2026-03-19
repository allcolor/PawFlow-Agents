# Guide de Développement - PawFlow

Ce guide s'adresse aux développeurs qui souhaitent étendre PawFlow en créant de nouvelles tâches, services, ou en contribuant au code source.

---

## Comment Créer une Nouvelle Task

### Étape 1 : Structure du fichier

Créez un nouveau fichier dans le répertoire `tasks/` correspondant à la catégorie :

```
tasks/
├── system/       # Tâches système (log, wait, fail, etc.)
├── io/           # Tâches I/O (fichiers, HTTP)
├── data/         # Tâches de transformation de données
└── control/      # Tâches de contrôle de flux
```

### Étape 2 : Implémenter la classe

```python
# tasks/data/my_transform.py
from typing import Dict, Any, List
from core import FlowFile, Task


class MyTransformTask(Task):
    """Transforme le contenu en majuscules."""

    TYPE = "myTransform"       # Type unique (identifiant)
    VERSION = "1.0.0"
    NAME = "My Transform"      # Nom affiché dans l'UI
    DESCRIPTION = "Convertit le contenu en majuscules"
    ICON = "🔠"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.encoding = self.config.get('encoding', 'utf-8')

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        content = flowfile.get_content().decode(self.encoding)
        flowfile.set_content(content.upper().encode(self.encoding))
        flowfile.set_attribute('transformed', 'true')
        return [flowfile]

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'encoding': {
                'type': 'string',
                'required': False,
                'description': 'Encodage du contenu',
                'default': 'utf-8'
            }
        }
```

### Étape 3 : Enregistrer la tâche

Ajouter l'import dans `tasks/__init__.py` dans la fonction `register_all_tasks()` :

```python
from tasks.data.my_transform import MyTransformTask
TaskFactory.register(MyTransformTask)
```

### Étape 4 : Utiliser dans un flow

```python
from core import Flow, FlowFile
from engine.executor import FlowExecutor
from tasks.data.my_transform import MyTransformTask

flow = Flow({'name': 'test'})
flow.tasks = {'transform': MyTransformTask({'encoding': 'utf-8'})}
flow.relations = []

executor = FlowExecutor()
result = executor.execute_flow(flow, input_flowfiles=[FlowFile(content=b'hello')])
# result.output_flowfiles[0].get_content() == b'HELLO'
```

### Étape 5 : Écrire les tests

```python
import pytest
from core import FlowFile
from tasks.data.my_transform import MyTransformTask


def test_uppercase():
    task = MyTransformTask({'encoding': 'utf-8'})
    ff = FlowFile(content=b'hello world')
    results = task.execute(ff)
    assert results[0].get_content() == b'HELLO WORLD'
    assert results[0].get_attribute('transformed') == 'true'

def test_empty_content():
    task = MyTransformTask({})
    ff = FlowFile(content=b'')
    results = task.execute(ff)
    assert results[0].get_content() == b''
```

### Points importants

- **TYPE doit être unique** : c'est l'identifiant pour la TaskFactory
- **execute() retourne toujours une List[FlowFile]** : même vide ou avec un seul élément
- **get_parameter_schema()** est utilisé par l'UI et l'API pour les formulaires de config
- **Config plate** : les tasks reçoivent un dict plat `{"key": "val"}`, PAS `{"parameters": {"key": "val"}}`
- **Utiliser `get_content()`/`set_content()`** au lieu de `.content` pour le support streaming
- **Services injectés** : accéder aux services partagés via `self.get_service("service_id")`

---

## Comment Créer un Nouveau Service

```python
# services/my_database.py
from typing import Dict, Any
from core import Service


class MyDatabaseService(Service):
    """Service de connexion à PostgreSQL."""

    TYPE = "myDatabase"
    NAME = "My Database"
    DESCRIPTION = "Connexion PostgreSQL"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.host = self.config.get('host', 'localhost')
        self.port = self.config.get('port', 5432)

    def connect(self):
        import psycopg2
        self._connection = psycopg2.connect(
            host=self.host, port=self.port,
            database=self.config.get('database'),
            user=self.config.get('user'),
            password=self.config.get('password'),
        )

    def disconnect(self):
        if self._connection:
            self._connection.close()

    def execute_query(self, query: str, params=()):
        cursor = self._connection.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()

    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'host': {'type': 'string', 'required': True},
            'port': {'type': 'integer', 'required': False, 'default': 5432},
            'database': {'type': 'string', 'required': True},
            'user': {'type': 'string', 'required': True},
            'password': {'type': 'secret', 'required': True},
        }
```

Les services sont automatiquement connectés au démarrage par le FlowExecutor et le ContinuousFlowExecutor. Les tasks peuvent y accéder via `self.get_service("service_id")`.

---

## Comment Créer un Plugin (.pfp)

### Structure du plugin

```
mon-plugin/
├── plugin.json          # Descripteur (obligatoire)
├── requirements.txt     # Dépendances pip (optionnel)
├── tasks/
│   └── mon_task.py      # Tâches personnalisées
├── services/
│   └── mon_service.py   # Services personnalisés
└── flows/
    └── mon_flow.json    # Flows pré-configurés
```

### plugin.json

```json
{
    "id": "com.example.mon-plugin",
    "name": "Mon Plugin",
    "version": "1.0.0",
    "author": "Auteur",
    "description": "Description du plugin",
    "min_pawflow_version": "1.0.0",
    "tasks": ["tasks/mon_task.py:MonTaskClass"],
    "services": ["services/mon_service.py:MonServiceClass"],
    "flows": ["flows/mon_flow.json"]
}
```

### Packager en .pfp

```python
from core.plugin import create_plugin_archive
create_plugin_archive("mon-plugin/", "mon-plugin-1.0.0.pfp")
```

### Installer

```python
from core.plugin import PluginManager
pm = PluginManager()
pm.install("mon-plugin-1.0.0.pfp")
pm.load_all()
```

Ou via l'API REST :
```bash
curl -X POST http://localhost:8000/api/v1/plugins/upload \
  -F "file=@mon-plugin-1.0.0.pfp" \
  -H "Authorization: Bearer <token>"
```

---

## API REST

L'API est accessible à `http://localhost:8000` avec documentation Swagger à `/docs`.

### Démarrer l'API

```bash
python -m api.app                    # port 8000
python -m api.app --port 9000        # port custom
python -m api.app --reload           # mode dev
```

### Authentification

```bash
# Login (si auth activée)
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' | jq -r .session_id)

# Utiliser le token
curl http://localhost:8000/api/v1/flows/ -H "Authorization: Bearer $TOKEN"

# Ou utiliser une API key
curl http://localhost:8000/api/v1/flows/ -H "Authorization: Bearer <api_key>"
```

### Endpoints principaux

| Préfixe | Description |
|---------|-------------|
| `/api/v1/auth` | Login, logout, users, API keys, OAuth2, rôles |
| `/api/v1/flows` | CRUD flows, validate, import/export |
| `/api/v1/execution` | Batch, continu (start/stop/inject), task actions |
| `/api/v1/monitoring` | Bulletins, provenance, streaming stats |
| `/api/v1/scheduler` | CRUD jobs CRON, start/stop scheduler |
| `/api/v1/tasks` | Task/service types et schémas de paramètres |
| `/api/v1/workers` | Workers distants, health, register/unregister |
| `/api/v1/plugins` | Install/uninstall/upload plugins |
| `/api/v1/system` | Health, info, security status |

---

## Lancer les Tests

```bash
# Tous les tests (758)
pytest tests/ -v

# API REST
pytest tests/test_api.py -v                  # 39 tests

# Exécution continue
pytest tests/test_continuous_executor.py -v  # 22 tests

# Sécurité + checkpoint
pytest tests/test_security_checkpoint.py -v  # 29 tests

# Avec couverture
pytest tests/ --cov=core --cov=engine --cov=tasks --cov=api --cov-report=term-missing
```

---

## Conventions de Code

### Nommage

- **Classes** : PascalCase (`LogTask`, `FlowExecutor`)
- **Fonctions/méthodes** : snake_case (`execute_flow`, `get_attribute`)
- **Variables** : snake_case (`input_directory`, `max_retries`)
- **Constantes de classe** : UPPER_CASE (`TYPE`, `VERSION`, `NAME`)
- **Fichiers** : snake_case (`log_task.py`, `flow_executor.py`)

### Style

- PEP 8
- Type hints sur les signatures publiques
- Docstrings pour les classes et méthodes publiques
- Imports groupés : standard, third-party, local

### Config des tasks

```python
# CORRECT : dict plat
task = MyTask({"key": "value", "other": "val"})

# INCORRECT : ne pas wrapper dans "parameters"
task = MyTask({"parameters": {"key": "value"}})  # NON
```

Le FlowParser gère le wrapping `parameters` pour les fichiers JSON, mais les tasks lisent toujours `self.config.get("key")` directement.

---

## Phases complétées

| Phase | Description | Statut |
|-------|-------------|--------|
| 1 | Core (FlowFile, Task, Service, Flow, Executor) | ✅ Done |
| 2 | Tasks de base (log, replaceText, getFile, putFile, etc.) | ✅ Done |
| 3 | Expression Language (`${...}`, Jinja2) | ✅ Done |
| 4 | +30 tasks (SQL, JSON, CSV, cache, compress, etc.) | ✅ Done |
| 5 | Services (DB, Cache, HTTP, LLM) | ✅ Done |
| 6 | Runtime (continu, scheduler, connections, backpressure) | ✅ Done |
| 7 | GUI Streamlit (5 pages), CLI | ✅ Done |
| 8 | Workers distants, streaming, plugins | ✅ Done |
| 9 | Sécurité (RBAC, OAuth2, sessions, API keys) | ✅ Done |
| 10 | API REST (FastAPI, 85+ endpoints, auth middleware) | ✅ Done |
| 10b | API Client, cluster mode, storage backends, NiFi converter | ✅ Done |
| 11 | Docker deployment (Dockerfile, docker-compose, documentation) | ✅ Done |
| 12 | Production hardening, observability, scaling | Planned |
