# Système de Provenance - PawFlow

Le système de provenance de PawFlow permet de tracer le cycle de vie complet de chaque FlowFile à travers le pipeline de traitement. Il enregistre tous les événements significatifs pour permettre l'audit, le débogage et la reconstruction du lignage des données.

---

## Objectifs

1. **Traçabilité complète** : Savoir où chaque FlowFile a été et ce qui lui est arrivé
2. **Audit** : Conserver l'historique des traitements pour la conformité
3. **Débogage** : Retracer les erreurs jusqu'à leur source
4. **Lignage** : Comprendre les relations parent/enfant entre FlowFiles
5. **Performance** : Statistiques par tâche et par flow

---

## Types d'Événements de Provenance

PawFlow définit **7 types d'événements** :

| Type | Quand | Contexte |
|------|-------|----------|
| **CREATE** | Création initiale d'un FlowFile | Entrée du flow |
| **RECEIVE** | Une tâche commence à traiter un FlowFile | Début d'exécution de tâche |
| **SEND** | FlowFile transmis sans modification | Sortie de tâche (contenu inchangé) |
| **MODIFY** | Contenu ou attributs modifiés | Sortie de tâche (contenu changé) |
| **CLONE** | FlowFile dupliqué pour branching | DAG avec plusieurs successeurs |
| **DROP** | FlowFile jeté après erreur | Échec après max_retries |
| **ROUTE** | FlowFile routé vers une sortie spécifique | RouteOnAttribute |

---

## ProvenanceEvent : Structure

```python
@dataclass
class ProvenanceEvent:
    event_id: str          # UUID unique de l'événement
    event_type: ProvenanceEventType
    timestamp: datetime

    # Identifiants
    flowfile_id: str                    # ID du FlowFile concerné
    parent_flowfile_ids: List[str]      # Parents (pour CLONE, MODIFY)
    child_flowfile_ids: List[str]       # Enfants
    task_id: str                        # Tâche qui a généré l'événement
    task_type: str                      # Type de la tâche
    flow_id: str                        # Flow concerné

    # Données
    content_size: int                   # Taille du contenu en bytes
    attributes: Dict[str, str]          # Copie des attributs au moment de l'événement
    details: str                        # Description textuelle
    duration_ms: float                  # Durée du traitement (ms)

    def to_dict(self) -> Dict[str, Any]:
        """Sérialisation en dictionnaire."""
```

---

## ProvenanceRepository : Stockage et Requêtes

### Initialisation

```python
from engine.provenance import ProvenanceRepository

# Créer un repository (max 100,000 événements par défaut)
repo = ProvenanceRepository(max_events=100000)
```

### Enregistrement (thread-safe, FIFO)

```python
repo.record(ProvenanceEvent(
    event_type=ProvenanceEventType.CREATE,
    flowfile_id="ff-123",
    flow_id="my-flow",
    details="FlowFile d'entrée"
))
```

Quand `max_events` est dépassé, les événements les plus anciens sont automatiquement supprimés (FIFO eviction).

### Filtrage

```python
# Par FlowFile
events = repo.get_events(flowfile_id="ff-123")

# Par tâche
events = repo.get_events(task_id="log-task")

# Par type d'événement
events = repo.get_events(event_type=ProvenanceEventType.MODIFY)

# Par flow
events = repo.get_events(flow_id="my-flow")

# Combiné avec limite
events = repo.get_events(flowfile_id="ff-123", event_type=ProvenanceEventType.MODIFY, limit=10)
```

### Reconstruction du lignage

```python
# Lignage complet d'un FlowFile (parents + enfants récursifs)
lineage = repo.get_lineage("ff-123")

for event in lineage:
    print(f"{event.event_type.value}: {event.flowfile_id[:8]}... → {event.details}")
```

Le lignage suit les relations `parent_flowfile_ids` et `child_flowfile_ids` récursivement pour reconstruire l'historique complet.

### Événements par flow

```python
events = repo.get_flow_events("my-flow")
# Retourne tous les événements triés par timestamp
```

### Statistiques

```python
stats = repo.to_dict()
# {
#   "total_events": 1500,
#   "max_events": 100000,
#   "events_by_type": {"CREATE": 100, "RECEIVE": 500, "MODIFY": 400, ...},
#   "events_by_task": {"log": 300, "transformJSON": 200, ...}
# }
```

### Nettoyage

```python
repo.clear()           # Vider le repository
repo.size()            # Nombre d'événements
```

---

## Intégration avec FlowExecutor

### Activation

```python
from engine.provenance import ProvenanceRepository
from engine.executor import FlowExecutor

repo = ProvenanceRepository()
executor = FlowExecutor(provenance=repo)

# La provenance est optionnelle : passer None (défaut) la désactive
executor_no_prov = FlowExecutor(provenance=None)
```

### Quand chaque événement est émis

#### CREATE — Entrée du flow

```python
# Dans execute_flow(), après création des FlowFiles d'entrée
for ff in flowfiles:
    self._record_event(ProvenanceEventType.CREATE, ff, flow.id,
                       details="FlowFile d'entrée")
```

#### RECEIVE — Début du traitement par une tâche

```python
# Dans _execute_task_with_retry(), avant task.execute()
self._record_event(ProvenanceEventType.RECEIVE, flowfile, flow_id,
                   task_id=task_id, task_type=task_type)
```

#### MODIFY / SEND — Sortie de tâche

```python
# Dans _execute_task_with_retry(), après task.execute()
# MODIFY si contenu ou attributs ont changé, sinon SEND
for out_ff in result:
    modified = (out_ff.content != original_content or
                dict(out_ff.attributes) != original_attrs)
    evt_type = ProvenanceEventType.MODIFY if modified else ProvenanceEventType.SEND
    self._record_event(evt_type, out_ff, flow_id, ...)
```

#### CLONE — Branching du DAG

```python
# Dans _execute_dag(), quand un résultat doit aller vers plusieurs successeurs
# Le premier successeur reçoit l'original, les suivants reçoivent des clones
for i, successor in enumerate(successors):
    if i == 0:
        task_queue[successor].extend(result)
    else:
        for r_ff in result:
            cloned = r_ff.clone()
            self._record_event(ProvenanceEventType.CLONE, cloned, flow.id,
                               parent_ids=[r_ff.process_id],
                               details=f"Clone pour branche {successor}")
```

#### DROP — Échec après retries

```python
# Dans _execute_task_with_retry(), après épuisement des retries
self._record_event(ProvenanceEventType.DROP, flowfile, flow_id,
                   task_id=task_id, task_type=task_type,
                   details=f"Erreur après {self.max_retries} retries: {last_error}")
```

### Provenance dans les résultats

Quand la provenance est activée, les statistiques sont incluses dans `ExecutionResult` :

```python
result = executor.execute_flow(flow, input_flowfiles=[ff])

if result.success and 'provenance' in result.statistics:
    prov_stats = result.statistics['provenance']
    print(f"Total événements: {prov_stats['total_events']}")
    print(f"Par type: {prov_stats['events_by_type']}")
```

---

## Exemple complet

```python
from core import Flow, FlowFile, Task
from engine.executor import FlowExecutor
from engine.provenance import ProvenanceRepository, ProvenanceEventType
from tasks.system.log_task import LogTask
from tasks.system.update_attribute import UpdateAttributeTask

# 1. Créer le repository
repo = ProvenanceRepository()

# 2. Créer un flow avec branching
flow = Flow({'name': 'Provenance Demo'})
flow.tasks = {
    'update': UpdateAttributeTask({'attributes': {'processed': 'true'}}),
    'log_a': LogTask({'message': 'Branche A'}),
    'log_b': LogTask({'message': 'Branche B'}),
}
flow.relations = [
    {'from': 'update', 'to': 'log_a'},
    {'from': 'update', 'to': 'log_b'},
]

# 3. Exécuter avec provenance
executor = FlowExecutor(max_retries=1, provenance=repo)
ff = FlowFile(content=b'hello world', attributes={'source': 'test'})
result = executor.execute_flow(flow, input_flowfiles=[ff])

# 4. Analyser
print(f"Succès: {result.success}")
print(f"Événements: {repo.size()}")

for event in repo.get_flow_events(flow.id):
    print(f"  {event.event_type.value:8s} | ff={event.flowfile_id[:8]}... "
          f"| task={event.task_id or '-':10s} | {event.details}")
```

---

## Thread-Safety

Le `ProvenanceRepository` utilise un `threading.Lock` pour garantir la sécurité en accès concurrent. Toutes les méthodes publiques (`record`, `get_events`, `get_lineage`, `get_flow_events`, `clear`, `size`, `to_dict`) sont thread-safe.

Cela permet une utilisation sûre avec le `FlowExecutor` qui exécute les tâches en parallèle via `ThreadPoolExecutor`.
