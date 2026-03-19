# OpenPaw Deployment Guide

Ce guide couvre les options de deploiement de OpenPaw : local, Docker, et production.

---

## Quick Start

### Local Development

```bash
# API REST
python -m uvicorn api.app:app --reload --port 8000

# GUI Streamlit (dans un autre terminal)
python -m streamlit run gui/main.py

# CLI
python cli.py run flows/demo_pipeline.json -v
```

- API : http://localhost:8000/docs
- GUI : http://localhost:8501

### Docker

```bash
# Demarrer API + GUI
docker compose up -d

# API : http://localhost:8000
# GUI : http://localhost:8501

# Arreter
docker compose down
```

### Docker avec PostgreSQL

```bash
docker compose --profile postgres up -d
# PostgreSQL accessible sur localhost:5432
```

### Build uniquement

```bash
docker compose build
```

---

## Configuration

### Variables d'environnement

Copier `.env.example` vers `.env` et personnaliser :

```bash
cp .env.example .env
```

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENPAW_SECRET_KEY` | Cle secrete pour les sessions et tokens | `changeme` |
| `OPENPAW_CORS_ORIGINS` | Origines CORS autorisees (separees par virgule) | `http://localhost:8501,http://localhost:8000` |
| `OPENPAW_RATE_LIMIT` | Activer le rate limiting | `false` |
| `OPENPAW_RATE_LIMIT_MAX` | Nombre max de requetes par fenetre | `100` |
| `OPENPAW_RATE_LIMIT_WINDOW` | Fenetre de rate limiting en secondes | `60` |
| `OPENPAW_MAX_BODY_SIZE` | Taille max du body HTTP en bytes | `10485760` (10 MB) |
| `POSTGRES_PASSWORD` | Mot de passe PostgreSQL (profil postgres) | `openpawsecret` |
| `OPENPAW_API_URL` | URL de l'API pour la GUI | `http://localhost:8000` |

---

## Architecture Docker

```
docker compose up -d
```

Lance deux services :

1. **api** : Serveur FastAPI (uvicorn) sur le port 8000
   - Health check automatique sur `/api/v1/system/health`
   - Volumes montes : `flows/`, `config/`, `plugins/`
   - Redemarrage automatique (`unless-stopped`)

2. **gui** : Interface Streamlit sur le port 8501
   - Demarre apres le health check de l'API
   - Communique avec l'API via le reseau Docker interne (`http://api:8000`)

3. **postgres** (optionnel, profil `postgres`) : PostgreSQL 16 sur le port 5432
   - Donnees persistees dans un volume Docker (`pgdata`)

---

## Production Checklist

### Securite

- [ ] Changer `OPENPAW_SECRET_KEY` avec une valeur aleatoire forte
- [ ] Activer le rate limiting (`OPENPAW_RATE_LIMIT=true`)
- [ ] Configurer les origines CORS strictement
- [ ] Activer l'authentification dans l'API (RBAC)
- [ ] Generer des API keys pour les integrations
- [ ] Restreindre `OPENPAW_MAX_BODY_SIZE` selon les besoins

### Reseau

- [ ] Placer un reverse proxy (nginx, Traefik) devant l'API
- [ ] Configurer HTTPS/TLS
- [ ] Ne pas exposer PostgreSQL sur le reseau public

### Monitoring

- [ ] Verifier le health check : `GET /api/v1/system/health`
- [ ] Surveiller les bulletins via `GET /api/v1/monitoring/bulletins`
- [ ] Configurer les alertes sur les logs Docker

### Sauvegarde

- [ ] Sauvegarder le volume `pgdata` si PostgreSQL est utilise
- [ ] Sauvegarder les repertoires `flows/`, `config/`, `plugins/`
- [ ] Exporter les flows via l'API (`GET /api/v1/flows/{id}/export`)

### Performance

- [ ] Ajuster `max_workers` dans les executors selon les ressources CPU
- [ ] Configurer le backpressure des connections selon la memoire disponible
- [ ] Surveiller le SpillTracker pour les fichiers volumineux

---

## Commandes utiles

```bash
# Logs en temps reel
docker compose logs -f

# Logs d'un service specifique
docker compose logs -f api

# Redemarrer un service
docker compose restart api

# Reconstruire apres modification du code
docker compose up -d --build

# Verifier l'etat des services
docker compose ps

# Acceder au shell d'un conteneur
docker compose exec api bash

# Lancer les tests dans le conteneur
docker compose exec api pytest tests/ -v
```
