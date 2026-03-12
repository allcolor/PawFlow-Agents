# PyFi2 - Documentation Complète

**PyFi2** est un framework de traitement de flux de données inspiré d'**Apache NiFi**, implémenté en Python. Il permet de construire, orchestrer et exécuter des pipelines de traitement de données avec support du parallélisme, de la gestion d'erreurs, du suivi de provenance et d'une interface graphique complète.

---

## Présentation

PyFi2 offre une architecture modulaire pour créer des flux de traitement de données (flows) composés de tâches (tasks) interconnectées. Chaque flux est représenté comme un **DAG (Directed Acyclic Graph)** où les noeuds sont des tâches et les arêtes définissent le flux de données entre elles.

### Caractéristiques principales

- **Architecture orientée DAG** : Exécution parallèle basée sur le tri topologique
- **FlowFiles** : Unités de données avec streaming (in-memory + disk-spill automatique)
- **68 tâches** : Système, I/O, Data, Control, AI — prêtes à l'emploi
- **5 services** : DB, Cache, HTTP, Distributed Cache, LLM (OpenAI/Anthropic)
- **3 modes d'exécution** : Batch, Continu (NiFi-style), Planifié (CRON)
- **Déploiement Docker** : docker-compose avec API + GUI + PostgreSQL optionnel
- **Exécution continue** : Queues avec backpressure, TTL, routing par relationship, transactions
- **Provenance** : Traçabilité complète du flux de données
- **Streaming** : Support fichiers volumineux avec spill-to-disk automatique (SpillTracker)
- **Workers distants** : Exécution distribuée via HTTP avec health checks et circuit breaker
- **Checkpointing** : Persistance des queues et crash recovery
- **Sécurité** : RBAC (4 rôles), OAuth2, API keys, sessions
- **Plugin system** : Archives .pfp pour tasks/services/flows personnalisés
- **API REST** : FastAPI avec 85+ endpoints (10 routeurs), auth middleware, OpenAPI auto-générée
- **API Client** : Client Python pour piloter PyFi2 depuis la GUI ou des scripts
- **Cluster** : Mode cluster avec coordination multi-noeud
- **GUI** : Interface graphique Streamlit (5 pages : Dashboard, Editor, Runtime, Monitoring, Settings)
- **CLI** : 4 commandes (run, validate, list-tasks, info)

---

## Architecture Générale

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           PyFi2 Framework                               │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐  ┌───────────────────┐  ┌───────────┐  ┌───────────┐ │
│  │    Core       │  │     Engine        │  │    GUI    │  │  API REST │ │
│  │              │  │                   │  │           │  │           │ │
│  │ - FlowFile   │  │ - FlowExecutor    │  │ Streamlit │  │ FastAPI   │ │
│  │ - Task       │  │ - ContinuousExec. │  │ 5 pages   │  │ 10 routers │ │
│  │ - Service    │  │ - Scheduler       │  │ Canvas    │  │ Auth MW   │ │
│  │ - Flow       │  │ - Provenance      │  │ Editor    │  │ OpenAPI   │ │
│  │ - Connection │  │ - Checkpoint      │  │           │  │           │ │
│  │ - Security   │  │ - Workers         │  │           │  │           │ │
│  │ - Plugins    │  │ - Versioning      │  │           │  │           │ │
│  │ - Streaming  │  │ - Parser/Valid.   │  │           │  │           │ │
│  └──────────────┘  └───────────────────┘  └───────────┘  └───────────┘ │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                       Tasks Library (68)                         │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌────────┐ │   │
│  │  │ System   │ │    IO    │ │   Data   │ │ Control │ │   AI   │ │   │
│  │  │ (10)     │ │  (20)    │ │  (27)    │ │  (10)   │ │  (1)   │ │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └─────────┘ └────────┘ │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                      Services (5)                                │   │
│  │  DBPool │ Cache │ HTTPClient │ DistributedCache │ LLMConnection │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### Composants principaux

#### **Core** (`core/`)
- **FlowFile** : Données avec streaming transparent (in-memory ↔ disk-spill)
- **Task** : Interface abstraite pour les tâches, avec services injectés
- **Service** : Composants partagés avec cycle de vie (connect/disconnect)
- **Flow** : Orchestrateur de tâches avec configuration complète
- **Connection** : Queues entre tasks avec backpressure, TTL, stats
- **Security** : RBAC, sessions, API keys, OAuth2
- **Plugin** : Système de plugins .pfp avec chargement dynamique
- **Streaming** : ContentReference + SpillTracker (ref-counting, orphan cleanup)
- **Factories** : TaskFactory et ServiceFactory pour l'enregistrement dynamique

#### **Engine** (`engine/`)
- **FlowExecutor** : Exécution batch (DAG, parallélisme, timeout, retry)
- **ContinuousFlowExecutor** : Exécution NiFi-style (queues, transactions, backpressure)
- **FlowScheduler** : Planification CRON avec persistance des jobs
- **CheckpointManager** : Persistance des queues, crash recovery
- **ProvenanceRepository** : Traçabilité des FlowFiles
- **FlowVersionManager** : Versioning, hot-swap, rollback
- **WorkerCoordinator** : Workers distants avec health checks, circuit breaker
- **WorkerServer/Client** : Communication HTTP avec auth API key
- **FlowFileSerializer** : Protocol binaire streaming pour workers

#### **Tasks** (`tasks/`) — 68 tâches
- **System** (10) : log, replaceText, wait, fail, updateAttribute, generateFlowFile, hashContent, listFiles, executeScript, reporting
- **IO** (20) : getFile, putFile, fetchHTTP, listenHTTP, sendEmail, notifySlack, getSFTP, putSFTP, getFTP, putFTP, publishKafka, consumeKafka, getS3, putS3, getGCS, putGCS, getAzureBlob, putAzureBlob, publishMQTT, consumeMQTT
- **Data** (27) : transformJSON, validateJSON, evaluateJSONPath, splitJSON, attributesToJSON, base64Encode, compressContent, convertCharset, convertCSVToJSON, convertJSONToCSV, countText, extractText, filterContent, executeSQL, putSQL, detectDuplicate, putCache, getCache, fetchDistributedMapCache, putDistributedMapCache, parseXML, transformXML, convertAvroToJSON, convertJSONToAvro, convertParquetToJSON, convertJSONToParquet, inferLLM
- **Control** (11) : splitContent, mergeContent, duplicateContent, funnel, routeOnAttribute, executeFlow, controlRate, wait, notify, inputPort, outputPort

#### **API** (`api/`) — 10 routeurs, 85+ endpoints
- auth, flows, execution, monitoring, scheduler, tasks, workers, plugins, system, websocket
- Auth middleware : Bearer token (session), API key, mode sans auth
- Documentation OpenAPI auto-générée à `/docs`

#### **GUI** (`gui/`) — 5 pages Streamlit
- Dashboard, Editor (React Flow), Runtime, Monitoring, Settings (+ Plugins, Security)

---

## Quick Start Docker

```bash
# Cloner et lancer avec Docker
git clone https://github.com/votre-org/pyfi2.git
cd pyfi2
cp .env.example .env
docker compose up -d

# API : http://localhost:8000/docs
# GUI : http://localhost:8501
```

Voir **[deployment.md](deployment.md)** pour les options de déploiement avancées.

---

## Installation et Prérequis

### Prérequis système
- **Python** : 3.10+
- **pip** : Gestionnaire de packages Python

### Installation

```bash
# Cloner le repository
git clone https://github.com/votre-org/pyfi2.git
cd pyfi2

# Créer un environnement virtuel (recommandé)
python -m venv venv
source venv/bin/activate  # Sur Windows: venv\Scripts\activate

# Installer les dépendances
pip install -r requirements.txt
```

### Vérification

```bash
pytest tests/ -v  # 758 tests
```

---

## Quick Start

### Exemple 1 : Flow simple (batch)

```python
from core import Flow, FlowFile
from engine.executor import FlowExecutor
from tasks.system.log_task import LogTask

flow = Flow({'name': 'Log Simple'})
flow.tasks = {'logger': LogTask({'message': 'Traitement: ${filename}', 'level': 'INFO'})}
flow.relations = []

ff = FlowFile(content=b'data', attributes={'filename': 'file1.txt'})
executor = FlowExecutor(max_workers=4, max_retries=3)
result = executor.execute_flow(flow, input_flowfiles=[ff])
print(f"Succès: {result.success}, durée: {result.duration_ms:.0f}ms")
```

### Exemple 2 : Exécution continue (NiFi-style)

```python
from core import Flow, FlowFile
from engine.continuous_executor import ContinuousFlowExecutor
from tasks.system.log_task import LogTask
from tasks.system.update_attribute import UpdateAttributeTask

flow = Flow({'name': 'Continuous'})
flow.tasks = {
    'process': UpdateAttributeTask({'set': {'processed': 'true'}}),
    'logger': LogTask({'message': 'Done!'}),
}
flow.relations = [{'from': 'process', 'to': 'logger'}]

executor = ContinuousFlowExecutor(flow)
executor.start()

# Injecter des FlowFiles
for i in range(10):
    executor.inject(FlowFile(content=f"data-{i}".encode()))

# Surveiller
print(executor.get_status())

executor.stop()
```

### Exemple 3 : API REST

```bash
# Démarrer le serveur
python -m api.app --port 8000

# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}'

# Lister les flows
curl http://localhost:8000/api/v1/flows/ \
  -H "Authorization: Bearer <session_id>"

# Documentation interactive
open http://localhost:8000/docs
```

### Exemple 4 : CLI

```bash
python cli.py run flows/demo_pipeline.json -v
python cli.py validate flows/demo_pipeline.json
python cli.py list-tasks
python cli.py info flows/demo_pipeline.json
```

### Exemple 5 : GUI

```bash
python -m streamlit run gui/main.py
# Ouvrir http://localhost:8501
```

---

## Structure du Projet

```
PyFi2/
├── core/                          # Core du framework
│   ├── __init__.py               # FlowFile, Task, Service, Flow, Factories
│   ├── base_task.py              # BaseTask avec services injectés
│   ├── base_service.py           # BaseService avec gestion de connexion
│   ├── connection.py             # Connection (queues, backpressure, TTL)
│   ├── security.py               # RBAC, sessions, API keys, OAuth2
│   ├── plugin.py                 # PluginManager, .pfp archives
│   ├── stream.py                 # ContentReference, SpillTracker
│   ├── bulletin.py               # BulletinBoard (notifications)
│   ├── task_state.py             # TaskStateManager
│   ├── process_group.py          # Process groups
│   ├── prioritizer.py            # Task prioritization
│   ├── expression.py             # Expression evaluation
│   ├── variable_resolver.py      # Résolution ${...}
│   └── signals.py                # Signal system
│
├── engine/                        # Moteur d'exécution
│   ├── executor.py               # FlowExecutor (batch)
│   ├── continuous_executor.py    # ContinuousFlowExecutor (NiFi-style)
│   ├── scheduler.py             # FlowScheduler (CRON)
│   ├── checkpoint.py            # CheckpointManager (crash recovery)
│   ├── provenance.py            # ProvenanceRepository
│   ├── flow_version.py          # FlowVersionManager
│   ├── remote_worker.py         # WorkerCoordinator (health, circuit breaker)
│   ├── worker_server.py         # WorkerServer (HTTP, API key auth)
│   ├── worker_client.py         # WorkerClient
│   ├── worker_protocol.py       # FlowFileSerializer (binary streaming)
│   ├── parser.py                # FlowParser
│   └── validator.py             # FlowValidator
│
├── api/                           # API REST FastAPI
│   ├── app.py                    # Application principale
│   ├── auth.py                   # Auth middleware
│   └── routers/                  # 10 routeurs modulaires
│       ├── auth_router.py        # Login, users, API keys, OAuth2, rôles
│       ├── flows_router.py       # CRUD flows, validate, import/export
│       ├── execution_router.py   # Batch, continu, inject, task actions
│       ├── monitoring_router.py  # Bulletins, provenance, streaming
│       ├── scheduler_router.py   # CRUD jobs CRON
│       ├── tasks_router.py       # Task/service types et schémas
│       ├── workers_router.py     # Workers, health
│       ├── plugins_router.py     # Install/uninstall plugins
│       └── system_router.py      # Health, info, security status
│
├── tasks/                         # 68 tâches
│   ├── system/                   # 10 tâches système
│   ├── io/                       # 20 tâches I/O
│   ├── data/                     # 27 tâches data (dont inferLLM)
│   └── control/                  # 11 tâches de contrôle
│
├── services/                      # 5 services
│   ├── db_connection_pool.py     # DBConnectionPool
│   ├── cache_service.py          # CacheService
│   ├── http_client_service.py    # HTTPClientService
│   ├── distributed_cache.py      # DistributedMapCacheService
│   └── llm_connection.py         # LLMConnectionService (OpenAI/Anthropic)
│
├── gui/                           # GUI Streamlit
│   ├── main.py                   # Point d'entrée
│   ├── pages/                    # 5 pages
│   ├── components/               # Composants réutilisables
│   ├── services/                 # Services GUI
│   └── utils/                    # Auth helpers
│
├── Dockerfile                     # Image Docker
├── docker-compose.yml             # Orchestration API + GUI + PostgreSQL
├── .env.example                   # Template de configuration
├── .dockerignore                  # Exclusions Docker
├── tests/                         # 758 tests
│   ├── test_executor.py          # 23 tests
│   ├── test_tasks.py             # 15 tests
│   ├── test_provenance.py        # 18 tests
│   ├── test_expression.py        # 17 tests
│   ├── test_new_tasks.py         # 33 tests
│   ├── test_phase5_tasks.py      # 26 tests
│   ├── test_sync.py              # 27 tests
│   ├── test_process_group_content.py # 19 tests
│   ├── test_new_tasks2.py        # 10 tests
│   ├── test_phase10_tasks.py     # 18 tests
│   ├── test_validator.py         # 15 tests
│   ├── test_runtime_infra.py     # 34 tests
│   ├── test_streaming.py         # 27 tests
│   ├── test_worker_protocol.py   # 17 tests
│   ├── test_continuous_executor.py # 22 tests
│   ├── test_plugin_system.py     # 17 tests
│   ├── test_llm.py               # 15 tests
│   ├── test_prioritizer_reporting.py # 15 tests
│   ├── test_security_checkpoint.py # 29 tests
│   ├── test_api.py               # 39 tests
│   ├── test_api_client.py
│   ├── test_cluster.py
│   ├── test_storage_backends.py
│   ├── test_new_io_tasks.py
│   ├── test_infra_p7.py
│   ├── test_parameter_context.py
│   ├── test_nifi_converter.py
│   └── test_p5_p7_optional.py
│
├── cli.py                         # CLI (run, validate, list-tasks, info)
├── flows/                         # Exemples de flows
├── docs/                          # Documentation
├── config/                        # Configuration et stockage
├── plugins/                       # Plugins installés
└── ROADMAP.md                     # Roadmap
```

---

## Documentation

- **[architecture.md](architecture.md)** : Architecture détaillée, FlowFile, Task, Service, Flow
- **[provenance.md](provenance.md)** : Système de provenance, événements, lignage
- **[tasks.md](tasks.md)** : Catalogue des tâches disponibles
- **[deployment.md](deployment.md)** : Guide de déploiement (local, Docker, production)
- **[development.md](development.md)** : Guide pour créer des tâches/services personnalisés
- **[01_DOCUMENTATION_TECHNIQUE.md](01_DOCUMENTATION_TECHNIQUE.md)** : Documentation technique complète
- **[02_REFERENCE_TASKS_SERVICES.md](02_REFERENCE_TASKS_SERVICES.md)** : Référence tasks/services

---

## Tests

```bash
# Tous les tests (758)
pytest tests/ -v

# Par module
pytest tests/test_api.py -v              # 39 tests API REST
pytest tests/test_continuous_executor.py -v  # 22 tests exécution continue
pytest tests/test_security_checkpoint.py -v  # 29 tests sécurité/checkpoint

# Avec couverture
pytest tests/ --cov=core --cov=engine --cov=tasks --cov=api --cov-report=html
```

---

## Démarrage rapide

| Interface | Commande | URL |
|-----------|----------|-----|
| Docker    | `docker compose up -d` | API :8000 + GUI :8501 |
| API REST  | `python -m api.app` | http://localhost:8000/docs |
| GUI       | `python -m streamlit run gui/main.py` | http://localhost:8501 |
| CLI       | `python cli.py run flows/demo.json` | — |
