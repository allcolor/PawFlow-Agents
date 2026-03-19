# PawFlow - Pipeline Framework

## Vue d'Ensemble

PawFlow est un framework Python de type Apache NiFi permettant de créer, déployer et monitorer des pipelines de données complexes.

### Principales Caractéristiques

- **Architecture modulaire** : Tâches, Services, Flux et Groupes
- **Format JSON déclaratif** : Les flux sont définis dans des fichiers JSON lisibles
- **Deux états** : Création (design) et Runtime (exécution)
- **Extensible** : Ajout facile de nouvelles tâches et services
- **Flow-based programming** : Les données circulent via des FlowFiles
- **Variables runtime** : Configuration overrideable au déploiement

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      GUI (Streamlit)                        │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │
│   │  Editor      │    │  Runtime     │    │  Monitor     │ │
│   │ (Design)     │    │  (Deploy)    │    │  (Dashboard) │ │
│   └──────────────┘    └──────────────┘    └──────────────┘ │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      Core Engine                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Repository  │  │  Executor    │  │  FlowFile        │  │
│  │  (Git/DB/FS) │  │              │  │  (Context)       │  │
│  └──────────────┘  └──────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Structure du Projet

```
pawflow/
├── core/                  # Interfaces abstraites et classes de base
│   ├── __init__.py       # Definitions (Task, Service, Flow, FlowFile)
│   ├── base_task.py      # Implémentation de base pour les tâches
│   ├── base_service.py   # Implémentation de base pour les services
│   └── flowfile.py       # Classe FlowFile
│
├── tasks/                 # Implémentations de tâches
│   ├── system/           # Tâches système (log, replace_text, wait, fail)
│   ├── data/            # Tâches de traitement de données
│   ├── io/              # Tâches d'entrée/sortie (HTTP, SFTP, S3, DB)
│   └── control/         # Tâches de contrôle (route, split, merge)
│
├── services/              # Implémentations de services
│   ├── auth/            # Services d'authentification (OAuth2, Basic Auth)
│   ├── connectivity/    # Services de connectivité (DB, SFTP, HTTP)
│   └── utils/           # Services utilitaires
│
├── engine/                # Moteur d'exécution
│   ├── executor.py      # Moteur d'exécution des flux
│   └── parser.py        # Parser et validateur de flux JSON
│
├── config/                # Configuration et stockage
│   ├── __init__.py      # ConfigManager et Config
│   └── storage/         # Implémentations de stockage (FS, SQLite, Git, PG)
│
├── gui/                   # Interface graphique (à implémenter)
│   ├── editor/          # GUI de création de flux
│   └── runtime/         # GUI de runtime et monitoring
│
├── docs/                  # Documentation
├── examples/              # Exemples de flux
├── tests/                 # Tests unitaires et d'intégration
└── test_flow.py           # Script de test
```

## Installation

```bash
# Cloner le repository
git clone https://github.com/your-org/pawflow.git
cd pawflow

# Installer les dépendances
pip install -r requirements.txt

# Lancer les tests
python test_flow.py
```

## Utilisation Rapide

### 1. Créer un flux simple

```python
from engine import FlowParser, FlowValidator, FlowExecutor
from core import FlowFile

# Configuration d'un flux
flow_config = {
    'id': 'mon-flux',
    'name': 'Mon Flux',
    'tasks': {
        'log1': {
            'type': 'log',
            'parameters': {'message': 'Début', 'level': 'INFO'}
        }
    },
    'relations': []
}

# Parser et valider
flow = FlowParser.parse(flow_config)
FlowValidator.validate(flow)

# Exécuter
executor = FlowExecutor()
input_ff = FlowFile(content=b'data', attributes={'filename': 'test.txt'})
result = executor.execute_flow(flow, [input_ff])
```

### 2. Créer une tâche personnalisée

```python
from core import Task, FlowFile
from typing import Dict, Any, List

class MyCustomTask(Task):
    TYPE = "my_custom"
    VERSION = "1.0.0"
    NAME = "Ma Tâche Personnalisée"
    DESCRIPTION = "Description"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.param1 = config.get('param1', 'default')
    
    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        # Traitement
        content = flowfile.get_content().decode()
        modified = content.upper()
        flowfile.set_content(modified.encode())
        return [flowfile]
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'param1': {
                'type': 'string',
                'required': True,
                'description': 'Paramètre 1'
            }
        }

# Enregistrer la tâche
from core import TaskFactory
TaskFactory.register(MyCustomTask)
```

### 3. Créer un service personnalisé

```python
from core import Service
from typing import Dict, Any

class MyService(Service):
    TYPE = "my_service"
    VERSION = "1.0.0"
    NAME = "Mon Service"
    DESCRIPTION = "Description"
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_key = config.get('api_key')
    
    def connect(self):
        # Établir la connexion
        pass
    
    def disconnect(self):
        # Fermer la connexion
        pass
    
    def get_parameter_schema(self) -> Dict[str, Any]:
        return {
            'api_key': {
                'type': 'string',
                'required': True,
                'description': 'Clé API'
            }
        }

# Enregistrer le service
from core import ServiceFactory
ServiceFactory.register(MyService)
```

## Format JSON des Flux

Voir `docs/01_DOCUMENTATION_TECHNIQUE.md` pour la spécification complète.

Exemple simplifié :

```json
{
  "$schema": "http://pawflow.org/schemas/flow-v1.json",
  "metadata": {
    "name": "mon-flux",
    "version": "1.0.0"
  },
  "parameters": {},
  "tasks": {
    "task_id": {
      "type": "replace_text",
      "parameters": {
        "search_pattern": "old",
        "replacement": "new"
      }
    }
  },
  "relations": [
    {
      "from": "entry_1",
      "to": "task_id",
      "type": "success"
    }
  ]
}
```

## Tâches Disponibles

### Tâches Système
- **log** : Logguer un message
- **replace_text** : Remplacer du texte (regex ou simple)
- **wait** : Attendre une durée
- **fail** : Échouer explicitement

### À Implémenter
- **script** : Exécuter un script Python
- **shell** : Exécuter une commande shell
- **http** : Appeler une API HTTP
- **sftp** : Opérations SFTP
- **s3** : Opérations AWS S3
- **db** : Opérations base de données
- **split** : Splitter un FlowFile
- **merge** : Fusionner des FlowFiles
- **route** : Router vers différentes branches

## Services Disponibles

### À Implémenter
- **oauth2_authenticator** : Authentification OAuth2
- **basic_auth** : Authentification HTTP Basic
- **db_connection** : Connexion base de données
- **sftp_connection** : Connexion SFTP
- **s3_connection** : Connexion AWS S3
- **http_connection** : Configuration HTTP pool

## Documentation Complète

- **Documentation Technique** : `docs/01_DOCUMENTATION_TECHNIQUE.md`
- **Référence des Tâches/Services** : `docs/02_REFERENCE_TASKS_SERVICES.md`

## Roadmap

### Version 1.0 (MVP)
- [x] Architecture core
- [x] Interfaces Task/Service/Flow
- [x] Tâches système de base
- [x] Moteur d'exécution
- [x] Parser et validateur
- [ ] GUI Streamlit (editor)
- [ ] GUI Streamlit (runtime)

### Version 1.1
- [ ] Tâches données (script, shell, convert)
- [ ] Tâches IO (HTTP, SFTP, S3, DB)
- [ ] Services de connectivité
- [ ] Système de variables runtime

### Version 1.2
- [ ] GUI complète
- [ ] Monitoring et logs
- [ ] Tests d'intégration
- [ ] Documentation utilisateur

## Contribution

1. Fork le repository
2. Créer une branche pour la feature
3. Commiter vos changements
4. Pusher vers la branche
5. Ouvrir une Pull Request

## License

MIT License - Voir le fichier LICENSE

## Auteur

PawFlow Team

---

**Note** : Ce projet est en cours de développement. De nombreuses fonctionnalités restent à implémenter. Consultez la roadmap pour plus de détails.