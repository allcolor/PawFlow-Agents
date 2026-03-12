# Catalogue des Tâches - PyFi2

Ce document décrit toutes les tâches disponibles dans PyFi2, organisées par catégorie.

---

## Organisation

PyFi2 regroupe les tâches en **4 catégories** :

1. **System** : Tâches système de base (log, wait, fail, etc.)
2. **IO** : Tâches d'entrée/sortie (fichiers, HTTP)
3. **Data** : Tâches de transformation de données
4. **Control** : Tâches de contrôle de flux

---

## Récapitulatif

| Catégorie | Tâche | Type | Description |
|-----------|-------|------|-------------|
| **System** | LogTask | `log` | Logguer un message |
| | ReplaceTextTask | `replaceText` | Remplacer du texte |
| | WaitTask | `wait` | Attendre une durée |
| | FailTask | `fail` | Échouer explicitement |
| | UpdateAttributeTask | `updateAttribute` | Modifier les attributs |
| **IO** | GetFileTask | `getFile` | Lire un fichier |
| | PutFileTask | `putFile` | Écrire un fichier |
| | FetchHTTPTask | `fetchHTTP` | Requêtes HTTP |
| **Data** | TransformJSONTask | `transformJSON` | Transformer JSON |
| **Control** | RouteOnAttributeTask | `routeOnAttribute` | Router par attribut |
| | SplitContentTask | `splitContent` | Découper un contenu |
| | MergeContentTask | `mergeContent` | Fusionner des contenus |

---

## Tâches Système

### LogTask

**TYPE** : `log` | **Fichier** : `tasks/system/log_task.py`

Loggue un message avec formatage. Supporte les placeholders `${attribut}`.

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `message` | string | Oui | - | Message à logguer |
| `level` | string | Non | INFO | Niveau (DEBUG, INFO, WARNING, ERROR) |
| `logger_name` | string | Non | - | Nom du logger |
| `include_attributes` | boolean | Non | false | Inclure les attributs dans le log |

```python
from tasks.system.log_task import LogTask

task = LogTask({
    'message': 'Traitement: ${filename}, taille: ${fileSize}',
    'level': 'INFO',
    'include_attributes': True
})
```

---

### ReplaceTextTask

**TYPE** : `replaceText` | **Fichier** : `tasks/system/replace_text.py`

Remplace du texte dans le contenu d'un FlowFile.

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `search_string` | string | Oui | - | Chaîne à rechercher |
| `replacement_string` | string | Non | "" | Chaîne de remplacement |
| `case_sensitive` | boolean | Non | true | Sensible à la casse |
| `regex` | boolean | Non | false | Utiliser une expression régulière |
| `multiline` | boolean | Non | false | Multi-lignes pour regex |

```python
from tasks.system.replace_text import ReplaceTextTask

# Remplacement simple
task = ReplaceTextTask({'search_string': 'old', 'replacement_string': 'new'})

# Remplacement regex
task = ReplaceTextTask({
    'search_string': r'\d+',
    'replacement_string': 'NUMBER',
    'regex': True
})
```

---

### WaitTask

**TYPE** : `wait` | **Fichier** : `tasks/system/wait_task.py`

Attend une durée avant de continuer.

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `duration` | integer | Oui | - | Durée d'attente |
| `duration_unit` | string | Non | MS | Unité (MS, SEC, MIN, HOUR) |

```python
from tasks.system.wait_task import WaitTask

task = WaitTask({'duration': 2, 'duration_unit': 'SEC'})
```

---

### FailTask

**TYPE** : `fail` | **Fichier** : `tasks/system/fail_task.py`

Échoue explicitement un FlowFile (utile pour tester le retry ou signaler une erreur métier).

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `message` | string | Non | "Task forced failure" | Message d'erreur |
| `terminate` | boolean | Non | true | Terminer le flow entier |

```python
from tasks.system.fail_task import FailTask

task = FailTask({'message': 'Validation échouée: données invalides'})
```

---

### UpdateAttributeTask

**TYPE** : `updateAttribute` | **Fichier** : `tasks/system/update_attribute.py`

Modifie les attributs d'un FlowFile (ajouter, modifier, supprimer).

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `set` | map | Non | {} | Attributs à ajouter/modifier |
| `delete` | list | Non | [] | Attributs à supprimer |

Les valeurs supportent les références `${attribut}` vers d'autres attributs.

```python
from tasks.system.update_attribute import UpdateAttributeTask

task = UpdateAttributeTask({
    'set': {
        'full.path': '${directory}/${filename}',
        'status': 'processed'
    },
    'delete': ['temp.key']
})
```

---

## Tâches IO

### GetFileTask

**TYPE** : `getFile` | **Fichier** : `tasks/io/get_file.py`

Lit un fichier depuis le système de fichiers.

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `input_directory` | string | Oui | - | Répertoire source |
| `file_filter` | string | Non | * | Filtre glob (ex: `*.csv`) |
| `recursive` | boolean | Non | false | Parcourir les sous-répertoires |
| `keep_source` | boolean | Non | true | Conserver le fichier source |

**Attributs ajoutés** : `filename`, `absolute.path`, `path`, `fileSize`

```python
from tasks.io.get_file import GetFileTask

task = GetFileTask({
    'input_directory': '/data/input',
    'file_filter': '*.json',
    'recursive': True
})
```

---

### PutFileTask

**TYPE** : `putFile` | **Fichier** : `tasks/io/put_file.py`

Écrit un FlowFile sur le système de fichiers.

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `output_directory` | string | Oui | - | Répertoire de destination |
| `conflict_resolution` | string | Non | replace | Stratégie si fichier existant (replace, fail, ignore, rename) |
| `create_dirs` | boolean | Non | true | Créer le répertoire si inexistant |

**Attributs ajoutés** : `output.path`, `output.filename`

```python
from tasks.io.put_file import PutFileTask

task = PutFileTask({
    'output_directory': '/data/output',
    'conflict_resolution': 'rename',
    'create_dirs': True
})
```

---

### FetchHTTPTask

**TYPE** : `fetchHTTP` | **Fichier** : `tasks/io/fetch_http.py`

Effectue une requête HTTP.

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `url` | string | Oui | - | URL de la requête |
| `method` | string | Non | GET | Méthode HTTP (GET, POST, PUT, DELETE, PATCH) |
| `headers` | map | Non | {} | En-têtes HTTP |
| `timeout` | integer | Non | 30 | Timeout en secondes |
| `body_source` | string | Non | none | Source du body (none, flowfile, config) |
| `body` | string | Non | - | Body si body_source=config |

**Attributs ajoutés** : `http.status.code`, `http.url`, `http.method`, `mime.type`, `fileSize`

```python
from tasks.io.fetch_http import FetchHTTPTask

# GET
task = FetchHTTPTask({'url': 'https://api.example.com/data'})

# POST avec body depuis le FlowFile
task = FetchHTTPTask({
    'url': 'https://api.example.com/submit',
    'method': 'POST',
    'headers': {'Content-Type': 'application/json'},
    'body_source': 'flowfile'
})
```

---

## Tâches Data

### TransformJSONTask

**TYPE** : `transformJSON` | **Fichier** : `tasks/data/transform_json.py`

Transforme du contenu JSON.

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `operation` | string | Oui | - | Opération (extract, set, delete, flatten) |
| `json_path` | string | Non | $ | Chemin JSON pour extract |
| `set_values` | map | Non | {} | Valeurs pour set |
| `delete_keys` | list | Non | [] | Clés pour delete |
| `output_format` | string | Non | json | Format de sortie |

#### Opérations

- **extract** : Extraire par chemin (`$.data.items`)
- **set** : Ajouter/modifier des valeurs
- **delete** : Supprimer des clés
- **flatten** : Aplatir (`{"a": {"b": 1}}` → `{"a.b": 1}`)

```python
from tasks.data.transform_json import TransformJSONTask

# Extraire
task = TransformJSONTask({'operation': 'extract', 'json_path': '$.data'})

# Modifier
task = TransformJSONTask({'operation': 'set', 'set_values': {'processed': True}})

# Supprimer
task = TransformJSONTask({'operation': 'delete', 'delete_keys': ['temp', 'debug']})

# Aplatir
task = TransformJSONTask({'operation': 'flatten'})
```

---

## Tâches de Contrôle

### RouteOnAttributeTask

**TYPE** : `routeOnAttribute` | **Fichier** : `tasks/control/route_on_attribute.py`

Route les FlowFiles vers différentes sorties selon leurs attributs.

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `routing_strategy` | string | Non | route_to_matched | Stratégie (route_to_matched, route_to_all) |
| `routes` | map | Oui | - | Routes avec conditions |

#### Opérateurs de condition

| Opérateur | Description |
|-----------|-------------|
| `equals` | Égalité exacte |
| `not_equals` | Différent |
| `contains` | Contient la sous-chaîne |
| `matches_regex` | Match une expression régulière |
| `greater_than` | Plus grand que |
| `less_than` | Plus petit que |
| `is_empty` | Attribut vide |
| `is_not_empty` | Attribut non vide |

**Attribut ajouté** : `route` (nom de la route choisie ou `unmatched`)

```python
from tasks.control.route_on_attribute import RouteOnAttributeTask

task = RouteOnAttributeTask({
    'routing_strategy': 'route_to_matched',
    'routes': {
        'csv': {'attribute': 'filename', 'operator': 'contains', 'value': '.csv'},
        'json': {'attribute': 'filename', 'operator': 'contains', 'value': '.json'},
    }
})
```

---

### SplitContentTask

**TYPE** : `splitContent` | **Fichier** : `tasks/control/split_content.py`

Découpe le contenu d'un FlowFile en plusieurs FlowFiles.

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `separator` | string | Non | \n | Séparateur |
| `keep_separator` | boolean | Non | false | Conserver le séparateur |
| `max_splits` | integer | Non | 0 | Max de découpes (0 = illimité) |

**Attributs ajoutés** : `fragment.index`, `fragment.count`, `fileSize`

```python
from tasks.control.split_content import SplitContentTask

# Split par ligne
task = SplitContentTask({'separator': '\n'})

# Split CSV limité à 10 fragments
task = SplitContentTask({'separator': ',', 'max_splits': 10})
```

**Exemple** : Input `"a,b,c"` avec separator `,` → 3 FlowFiles : `"a"`, `"b"`, `"c"`

---

### MergeContentTask

**TYPE** : `mergeContent` | **Fichier** : `tasks/control/merge_content.py`

Fusionne plusieurs FlowFiles en un seul. Accumule les FlowFiles dans un buffer interne.

| Paramètre | Type | Requis | Défaut | Description |
|-----------|------|--------|--------|-------------|
| `separator` | string | Non | \n | Séparateur entre contenus |
| `min_entries` | integer | Non | 2 | Nombre minimum avant fusion |
| `header` | string | Non | - | En-tête ajouté au début |
| `footer` | string | Non | - | Pied de page ajouté à la fin |

**Attributs ajoutés** : `merge.count`, `fileSize`

```python
from tasks.control.merge_content import MergeContentTask

task = MergeContentTask({
    'separator': ',',
    'min_entries': 3,
    'header': 'col1,col2\n',
    'footer': '\n# EOF'
})
```

**Note** : MergeContent utilise un buffer interne (stateful). Non thread-safe si la même instance est utilisée par plusieurs threads.

---

## Créer une tâche personnalisée

Voir [development.md](development.md) pour le guide complet.

```python
from core import FlowFile, Task

class MyTask(Task):
    TYPE = "myCustom"
    NAME = "Ma Tâche"

    def get_parameter_schema(self):
        return {'param': {'type': 'string', 'required': True}}

    def execute(self, flowfile):
        # Traitement...
        flowfile.set_attribute('processed', 'true')
        return [flowfile]
```
