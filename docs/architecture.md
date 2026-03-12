# Architecture Détaillée - PyFi2

Ce document décrit l'architecture interne de PyFi2, ses composants principaux et leurs interactions.

---

## Vue d'ensemble

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                              PyFi2 Architecture                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐  ┌───────────────────┐  ┌──────────┐  ┌────────────────┐ │
│  │    Core       │  │      Engine       │  │   GUI    │  │   API REST     │ │
│  │              │  │                   │  │ Streamlit│  │   FastAPI      │ │
│  │ FlowFile     │  │ FlowExecutor      │  │ 5 pages  │  │ 10 routeurs    │ │
│  │ Task/Service │  │ ContinuousExec.   │  │ Canvas   │  │ Auth MW       │ │
│  │ Flow         │  │ Scheduler (CRON)  │  │ Editor   │  │ 85+ endpoints │ │
│  │ Connection   │  │ CheckpointMgr     │  │ Monitor  │  │ OpenAPI       │ │
│  │ Security     │  │ Provenance        │  │          │  │               │ │
│  │ Plugin       │  │ VersionManager    │  │          │  │               │ │
│  │ SpillTracker │  │ WorkerCoordinator │  │          │  │               │ │
│  └──────────────┘  └───────────────────┘  └──────────┘  └────────────────┘ │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                     Tasks (68) + Services (5)                        │   │
│  │  System(10) │ IO(20) │ Data(27) │ Control(11) │ AI(1) │ 5 Services │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## FlowFile : Structure et Cycle de Vie

### Définition

Le **FlowFile** est l'unité fondamentale de données dans PyFi2. Il contient du contenu binaire et des attributs metadata, avec support transparent du streaming et du disk-spill.

### Structure

```python
class FlowFile:
    _content_ref: ContentReference  # In-memory ou disk-backed (transparent)
    attributes: Dict[str, str]      # Metadata clé-valeur
    process_id: str                 # UUID unique
    created_at: datetime            # Timestamp de création
```

### API

```python
# Attributs
flowfile.get_attribute('key', 'default')
flowfile.set_attribute('key', 'value')
flowfile.delete_attribute('key')
flowfile.get_attributes()          # copie du dict

# Contenu (backward-compatible)
content = flowfile.get_content()   # bytes (charge en mémoire si spilled)
flowfile.set_content(b'data')      # auto-spill si > SPILL_THRESHOLD

# Streaming (nouveau — pour fichiers volumineux)
stream = flowfile.get_content_stream()   # BinaryIO (BytesIO ou file handle)
flowfile.set_content_from_stream(stream, size_hint=10_000_000)

# Taille et état
flowfile.size()                    # int (sans charger le contenu)
flowfile.is_empty()                # bool
flowfile.is_content_on_disk        # bool

# Clonage
clone = flowfile.clone(deep=True)  # deep=True: copie indépendante
clone = flowfile.clone(deep=False) # deep=False: partage via ref-counting
```

### Streaming et Disk-Spill (ContentReference + SpillTracker)

Les FlowFiles supportent le streaming transparent :
- **Contenu < SPILL_THRESHOLD** (10 MB) : stocké en mémoire
- **Contenu ≥ SPILL_THRESHOLD** : automatiquement spilled sur disque

Le `SpillTracker` gère le ref-counting et le nettoyage des fichiers temporaires :
```python
from core.stream import get_spill_tracker
stats = get_spill_tracker().get_stats()
# {active_spill_files, total_bytes_on_disk, total_spill_count, total_cleaned, ...}
```

---

## Task : Interface et Services Injectés

```python
class Task:
    TYPE: str           # Identifiant unique
    VERSION: str        # Version sémantique
    NAME: str           # Nom affiché
    DESCRIPTION: str    # Description
    ICON: str           # Icône pour la GUI

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        """Exécuter la tâche. Retourne 0, 1, ou N FlowFiles."""

    def get_parameter_schema(self) -> Dict[str, Any]:
        """Schema des paramètres pour validation et UI."""

    # Services injectés par l'executor
    def get_service(self, service_id: str) -> Any:
        """Accéder à un service partagé."""

    def set_services(self, services: Dict[str, Any]):
        """Appelé par l'executor pour injecter les services."""
```

### Configuration

Les tasks reçoivent un dict plat :
```python
task = LogTask({"message": "hello", "level": "INFO"})
# Accès : self.config.get("message")
```

---

## Connection : Queues avec Backpressure

Les `Connection` relient les tasks dans le mode continu. Chaque connection est une queue FIFO.

```python
class Connection:
    source_id: str
    target_id: str
    relationship: str            # "success", "failure", "matched", etc.
    max_queue_size: int = 10000  # Backpressure par nombre
    max_queue_bytes: int = ...   # Backpressure par taille
    flowfile_ttl_seconds: float  # TTL des FlowFiles (0 = pas de TTL)

    def enqueue(ff) -> bool      # False si backpressure
    def dequeue() -> FlowFile
    def peek() -> FlowFile       # Sans retirer
    def is_empty() -> bool
    def queue_size() -> int
    def drain_expired() -> list  # FlowFiles expirés (TTL)
```

---

## Deux Modes d'Exécution

### 1. FlowExecutor (Batch)

Exécute le DAG niveau par niveau avec parallélisme :

```python
executor = FlowExecutor(
    max_workers=10,        # Threads parallèles
    max_retries=3,         # Retries par tâche
    flow_timeout=300,      # Timeout global (s)
    provenance=repo,       # ProvenanceRepository (optionnel)
)
result = executor.execute_flow(flow, input_flowfiles=[ff])
```

Séquence : tri topologique → niveaux → parallel execution → clone si branching → résultat.

### 2. ContinuousFlowExecutor (NiFi-style)

Exécution continue avec queues et transactions :

```python
executor = ContinuousFlowExecutor(
    flow,
    max_workers=8,
    max_retries=3,
    enable_checkpoints=True,
    checkpoint_interval=30.0,
)
executor.start()
executor.inject(FlowFile(content=b"data"))
executor.get_status()
executor.stop()
```

**Transaction model :**
1. **Peek** : FlowFile lu de la queue d'entrée (sans retirer)
2. **Execute** : tâche exécutée
3. **Commit** : FlowFile retiré de l'entrée, résultats envoyés en sortie
4. **Rollback** : FlowFile reste dans la queue, tâche passe en ERROR

**Routing par relationship :**
- FlowFiles avec attribut `route.relationship` → connection correspondante
- Fallback → toutes les connections sortantes

**Failure routing (penalty box) :**
- Si une connection "failure" existe → FlowFile déqueué et routé là
- Sinon → FlowFile reste dans la queue, tâche en ERROR, backpressure cascade

**Hot-swap :**
```python
executor.update_task("task_id", new_config)    # Change config sans perte
executor.update_flow(new_flow)                  # Mise à jour structurelle
```

---

## Checkpointing et Crash Recovery

Le `CheckpointManager` sauvegarde périodiquement l'état des queues :

```python
mgr = CheckpointManager(flow_id="my_flow", max_checkpoints=5)
mgr.save_checkpoint(connections, task_states, flow_version)
data = mgr.load_latest_checkpoint()
flowfiles = mgr.restore_flowfiles(data)
```

Format : JSON avec contenu FlowFile en base64 (petits) ou fichiers (> 256 KB).

---

## Workers Distants

### WorkerCoordinator

Distribue les tâches sur des workers locaux ou distants :

```python
coord = WorkerCoordinator(
    heartbeat_timeout_seconds=60,
    max_consecutive_failures=5,
)
coord.register_worker("remote-1", "192.168.1.10", 9000)
coord.get_health_summary()
```

**Circuit breaker** : après N échecs consécutifs, worker → OFFLINE.

### WorkerServer / WorkerClient

Communication HTTP avec protocole binaire streaming et auth API key :

```python
server = WorkerServer(port=9000, api_key="secret")
server.start()

client = WorkerClient("192.168.1.10", 9000, api_key="secret")
result = client.execute_task("log", config, content, attributes)
```

---

## Sécurité (RBAC)

### Rôles et Permissions

| Rôle | Permissions |
|------|-------------|
| **admin** | Tout : users, plugins, settings, flows, execute, monitor |
| **editor** | flows CRUD, execute, monitor, services |
| **operator** | execute, monitor |
| **viewer** | monitor (lecture seule) |

### SecurityManager

```python
security = SecurityManager.get_instance()
security.enable_auth(True)
session = security.authenticate("admin", "password")
security.check_permission(session, "flow.edit")
security.generate_api_key("Description")
security.set_oauth_config("google", {...})
```

---

## Plugin System (.pfp)

Les plugins sont des archives ZIP contenant tasks, services et flows :

```
plugin.json, tasks/, services/, flows/, requirements.txt
```

```python
pm = PluginManager()
pm.install("plugin.pfp")
pm.load_all()
pm.list_plugins()
pm.uninstall("plugin-id")
```

---

## API REST (FastAPI)

10 routeurs, 85+ endpoints, auth middleware, documentation OpenAPI à `/docs`.

```bash
python -m api.app --port 8000
```

| Routeur | Préfixe | Description |
|---------|---------|-------------|
| auth | `/api/v1/auth` | Login, users, API keys, OAuth2 |
| flows | `/api/v1/flows` | CRUD flows, validate, import/export |
| execution | `/api/v1/execution` | Batch, continu, inject, task actions |
| monitoring | `/api/v1/monitoring` | Bulletins, provenance, streaming |
| scheduler | `/api/v1/scheduler` | CRUD jobs CRON |
| tasks | `/api/v1/tasks` | Types, schémas de paramètres |
| workers | `/api/v1/workers` | Register, health, reset |
| plugins | `/api/v1/plugins` | Install/uninstall/upload |
| system | `/api/v1/system` | Health, info, security status |

---

## Scheduler (CRON)

```python
scheduler = FlowScheduler()
scheduler.add_job("daily", "flows/pipeline.json", "0 6 * * *")
scheduler.start()
scheduler.save_jobs()
```

Format CRON standard : `minute hour day month weekday`

---

## Provenance

Le `ProvenanceRepository` trace le cycle de vie de chaque FlowFile :

```python
repo = get_provenance_repository()
events = repo.get_events(flowfile_id="abc", limit=100)
lineage = repo.get_lineage("abc")  # Lignage complet
stats = repo.to_dict()
```

Types d'événements : CREATE, RECEIVE, SEND, MODIFY, CLONE, DROP, ROUTE.

---

## Cluster Mode

Le module `engine/cluster.py` fournit un mode cluster pour la coordination multi-noeud :

- Election de leader pour eviter les conflits d'execution
- Synchronisation d'etat entre les noeuds
- Distribution automatique des flows sur les workers disponibles
- Health monitoring inter-noeuds

---

## API Client

Le client Python (`gui/services/api_client.py`) permet de piloter PyFi2 depuis la GUI ou des scripts :

```python
from gui.services.api_client import APIClient

client = APIClient(base_url="http://localhost:8000")
client.login("admin", "admin")

# Operations sur les flows
flows = client.list_flows()
client.start_continuous(flow_id)
client.inject_flowfile(flow_id, content, attributes)
```

---

## Deploiement Docker

PyFi2 fournit un `Dockerfile` et un `docker-compose.yml` pour le deploiement containerise :

- **api** : Serveur FastAPI (uvicorn) sur le port 8000
- **gui** : Interface Streamlit sur le port 8501
- **postgres** (optionnel) : PostgreSQL 16 avec persistence

Voir **[deployment.md](deployment.md)** pour le guide complet.
