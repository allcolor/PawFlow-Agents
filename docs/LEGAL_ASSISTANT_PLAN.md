# PawFlow pour un cabinet d'avocat — analyse et propositions

> Document d'analyse produit. Pas un plan d'implémentation figé : sert de base
> de décision pour cadrer le MVP et les itérations suivantes.

## 1. Vision

L'objectif n'est pas un chatbot juridique générique, mais un système qui tient
quatre rôles à la fois pour l'avocate qui l'utilise :

- **Assistant** — retrouve l'information (texte de loi, pièce du dossier,
  historique d'un client) plus vite qu'elle ne le ferait seule.
- **Collègue** — relit un raisonnement, signale une contradiction, propose un
  angle qu'elle n'avait pas considéré, sans jamais décider à sa place.
- **Secrétaire** — gère les tâches répétitives et suivables : accusés de
  réception, prise de RDV, rappels, préparation de courriers types.
- **Expert** — connaît le droit applicable, cite les textes exacts avec leur
  version en vigueur, et sait dire "je ne sais pas" plutôt que d'inventer.

Le risque principal du projet n'est pas technique, il est de confiance : une
hallucination sur un article de loi ou un délai de procédure raté n'est pas
un bug UX, c'est une faute professionnelle. Toute l'architecture ci-dessous
est pensée autour de cette contrainte plutôt qu'en périphérie.

## 2. Ce qui existe déjà dans PawFlow et se réutilise tel quel

| Besoin | Brique PawFlow existante |
|---|---|
| Dossier client + historique | Une conversation par client (persistante, multi-session) |
| Contexte durable sur un dossier | Memory + Knowledge Graph scopés à la conversation |
| Notes d'entretien à l'oral | Chat vocal temps réel (OpenAI Realtime / Gemini Live, barge-in) |
| Envoi de courrier/relance | Tâche `sendEmail` (SMTP, OAuth2 Gmail/Microsoft 365) |
| Scraping de textes de loi / sites gouvernementaux | Tâche `fetchHTTP` / `scraplingFetch` (anti-bot, JS rendering) |
| Automatisations (relance auto, génération de courrier) | Moteur de flows (DAG, CRON, triggers) |
| Confidentialité | Self-hosted — aucune donnée client ne transite par un SaaS tiers par défaut |
| Pièces du dossier | Filesystem par conversation via le relay |

## 3. Ce qui manque et vient d'être construit

### 3.1 Tâche calendrier (`manageCalendar`) — livrée dans ce cycle

Nouvelle tâche `tasks/io/manage_calendar.py`, sur le même modèle que
`sendEmail` (mêmes conventions de credentials/erreurs) :

- **Provider `google`** — Google Calendar API v3, OAuth2 refresh-token
  (client_id/client_secret/refresh_token, identique au flow OAuth2 déjà
  utilisé pour Gmail). Actions `list` / `create` / `update` / `delete`.
- **Provider `caldav`** — CalDAV générique (Nextcloud, Radicale, iCloud, la
  plupart des serveurs auto-hébergés), auth Basic, `PUT`/`DELETE` de
  ressources iCalendar (.ics) et une requête `REPORT` (`calendar-query`) pour
  lister les événements sur une fenêtre de temps.

Couvert par 13 tests (mockés, sans appel réseau réel), documenté dans
`docs/tasks.md` et `CHANGELOG.md`.

Cela débloque directement les flows RDV et rappels décrits en section 5.

### 3.2 Base de connaissances juridique indexée

`fetchHTTP` à la demande est trop lent et fragile pour être la source
principale (rate limiting, mise en page qui change). Le bon pattern : un flow
CRON qui indexe périodiquement les textes pertinents (codes, jurisprudence
citée) dans la Knowledge Graph / memory avec embeddings, en conservant
systématiquement la source et la date de version. Le chat interroge cette
base locale en priorité, et ne re-scrape que pour vérifier une mise à jour ou
un texte absent.

### 3.3 Suivi des délais de procédure

La fonctionnalité à plus haute valeur (et plus haut risque si absente).
Structurellement : une entité "délai" par dossier (type d'acte, date de
déclenchement, durée légale, date butoir calculée, statut), stockée dans la
Knowledge Graph du dossier, avec un flow CRON quotidien qui scanne les délais
à venir et déclenche des rappels (`manageCalendar` + `sendEmail`/notification
in-app) à J-30, J-7, J-1. Le calcul de la date butoir elle-même doit être
vérifiable par l'avocate, jamais appliqué sans confirmation humaine.

## 4. Fonctionnalités proposées, par catégorie

### 4.1 Dossier client
- Fiche client (identité, coordonnées, type d'affaire, statut : actif / en
  attente de pièces / clôturé).
- Chronologie automatique du dossier reconstruite depuis les échanges,
  documents versés et actions effectuées.
- Recherche transversale ("tous les dossiers où on invoque l'article X").

### 4.2 Recherche juridique
- Réponse toujours sourcée : article exact, texte, date de version.
- Distinction claire entre "texte de loi vérifié dans la base indexée" et
  "synthèse générée à interpréter avec prudence" — marquage visuel différent.
- Veille juridique : alerte quand un texte suivi par le cabinet change.

### 4.3 Délais et procédure
- Calcul assisté de délai (jamais appliqué automatiquement).
- Vue "prochaines échéances tous dossiers" en page d'accueil.
- Rappels multi-canal (email, notification in-app, Telegram si le cabinet
  l'utilise déjà comme client agent).

### 4.4 Communication
- Brouillons de courrier générés à partir de templates + variables du
  dossier, jamais envoyés sans clic de validation humaine.
- Accusé de réception automatique sur les emails entrants identifiés comme
  liés à un dossier existant.
- Résumé automatique d'un échange email long avant de répondre.

### 4.5 Calendrier et RDV
- Prise de RDV client avec confirmation automatique (`manageCalendar` +
  `sendEmail`).
- Rappel 24h avant, avec possibilité de reprogrammer par lien.
- Vue calendrier globale du cabinet croisée avec les échéances de procédure.

### 4.6 Chat temps réel
- Dictée de notes d'entretien pendant ou juste après un RDV, transcrite et
  versée automatiquement dans la conversation du dossier concerné.
- Mode "collègue" : poser une question à voix haute sur un point de
  procédure pendant qu'on prépare un dossier, sans lâcher le clavier/dossier
  papier.

### 4.7 Rédaction assistée
- Premier jet de courrier, mise en demeure, conclusion — toujours présenté
  comme brouillon à revoir, jamais comme document final.
- Relecture de cohérence (dates, montants, noms de parties) entre le
  brouillon et les pièces du dossier.

### 4.8 Hors scope MVP (à ne pas construire tout de suite)
- Facturation / suivi du temps facturable — utile mais indépendant du cœur
  de la proposition, à traiter après le MVP si le besoin se confirme.
- Signature électronique — intégration tierce à évaluer séparément.

## 5. UI simplifiée dédiée

Pas le dashboard PawFlow actuel (pensé pour un opérateur technique), un skin
dédié réutilisant les mêmes API/SSE, avec trois écrans principaux.

### Écran 1 — Liste des dossiers
- Sidebar : recherche, tri par échéance la plus proche (pas par activité
  récente), badge rouge/orange sur les dossiers avec délai à moins de 7 jours.
- Chaque ligne : nom client, type d'affaire, statut, prochaine échéance.

### Écran 2 — Vue dossier
- Chat au centre (fil de conversation avec l'assistant sur ce dossier).
- Panneau latéral droit :
  - Pièces du dossier (upload, aperçu).
  - Échéances de ce dossier.
  - Actions rapides : envoyer un email, planifier un RDV, générer un
    courrier type — plutôt que de taper une commande.
- Distinction visuelle nette entre réponse de l'assistant sourcée (texte de
  loi vérifié) et brouillon/suggestion à valider.

### Écran 3 — Vue échéances transversale
- Calendrier global tous dossiers confondus — remplace l'agenda du cabinet,
  doit être la page d'accueil par défaut, pas une sous-page.
- Filtrage par type (RDV client / délai de procédure / audience).

## 6. Mapping des quatre rôles

| Rôle | Ce que ça veut dire concrètement ici |
|---|---|
| **Assistant** | Recherche instantanée dans la base juridique indexée et dans l'historique du dossier ; retrouve une pièce ou un échange en une phrase. |
| **Collègue** | Relit un brouillon de conclusion, signale une incohérence de date/montant, propose un angle transversal en repérant des dossiers similaires dans l'historique du cabinet — toujours en suggestion, jamais en décision. |
| **Secrétaire** | Accusés de réception, prise de RDV, rappels, préparation de courriers types — tout ce qui est répétitif et suivable par un flow. |
| **Expert** | Cite le texte exact en vigueur avec sa source, calcule un délai de procédure en le présentant comme "à vérifier", jamais comme un fait établi. |

## 7. Garde-fous non négociables

1. Toute affirmation de droit cite sa source exacte (article, texte, date de
   version) — jamais servie comme fait sans cette citation.
2. Aucun envoi automatique de courrier/email vers un tiers sans validation
   humaine explicite — les flows préparent, jamais n'expédient seuls.
3. Tout calcul de délai de procédure est présenté comme une proposition à
   vérifier, jamais appliqué ou communiqué sans confirmation humaine.
4. Confidentialité : vérifier que les LLM providers utilisés pour ce cabinet
   n'ont pas de rétention des prompts côté fournisseur si le contenu est
   sensible (provider local, ou engagement contractuel de non-rétention).

## 8. Ordre de priorité proposé (MVP → V1)

1. **Dossier-client + base juridique indexée avec citations** — sans ça, rien
   d'autre n'a de valeur différenciante.
2. **Suivi des délais de procédure** — plus haute valeur et plus haut risque
   si absent.
3. **Email semi-automatisé** — préparé, jamais envoyé sans clic.
4. **Calendrier/RDV** (`manageCalendar`, livré) — la brique la moins
   spécifique au droit, mais qui ferme la boucle secrétariat.
5. **UI dédiée** — une fois les flows validés en usage réel via le chat
   standard, pour éviter de construire une interface autour de
   fonctionnalités pas encore éprouvées.

## 9. Packaging en .pfp — URL dédiée, bouton, endpoint par défaut

Contrainte posée : ce livrable doit être un .pfp installable ; une fois
installé, l'interface avocat doit être joignable (a) via une URL dédiée et
(b) via un bouton dans l'interface PawFlow principale ; et l'utilisateur doit
pouvoir choisir, après connexion, un endpoint par défaut — depuis
l'interface PawFlow principale ou depuis l'interface dédiée elle-même.

### 9.1 Ce qui est déjà couvert par le format .pfp actuel

Le bouton dans l'interface principale ne demande rien de nouveau : un
ui_extension peut déclarer un slot action_menu ou header_actions (voir
docs/PFP_DEVELOPER_GUIDE.md, section UI Extensions) qui ouvre un lien ou un
panneau. Le reste du livrable (agents des quatre rôles, skill juridique,
task_def/flow pour le CRON de suivi des délais, service_definition pour
la base juridique indexée) rentre sans friction dans les types d'objets déjà
supportés (agent, skill, flow, task_def, service_provider, ui_extension).

### 9.2 Ce qui manque : pas de route page entière dédiée

Le contrat ui.v1 des ui_extension (_UI_ASSET_EXTENSIONS dans
core/pfp_package/_pp_base.py) exclut délibérément .html : les assets d'une
extension s'injectent en JS/CSS dans la page chat existante (slots,
panneaux via pfp.ui.openPanel), toujours sur la même origine et le même
domaine de confiance que le chat. Il n'existe aujourd'hui aucun type d'objet
.pfp qui serve une page complète à sa propre URL (/legal/... ou
équivalent), séparée du shell chat. Une interface dédiée avec sa propre URL
n'est donc pas un détail d'implémentation ici : c'est une capacité PawFlow à
construire avant de pouvoir livrer ce .pfp.

Proposition minimale, cohérente avec le modèle de sécurité existant :

- Nouveau type d'objet installable, par exemple web_app : le manifeste
  déclare un dossier d'assets statiques (html/js/css) plus une route stable
  (/apps/<package>/). PawFlow sert ces fichiers derrière la même session
  authentifiée que le chat (cookie/token existant), donc pas de second login.
- Comme il s'agit d'une route séparée (pas d'injection dans le DOM du chat),
  servir du .html y est acceptable sans reproduire le risque documente pour
  ui_extension (tous les extensions partagent un même DOM/window) —
  mais ça doit rester examiné à l'install comme les autres objets
  code-porteurs (scan à l'inspect, taille/hash affichés, consentement
  explicite), et cette page ne doit pas pouvoir usurper l'origine du chat ni
  lire son DOM.
- Le bouton de l'écran principal (ui_extension / header_actions) devient
  alors un simple lien vers /apps/legal/..., pas un panneau injecté.

Tant que web_app n'existe pas, la seule solution livrable dès aujourd'hui
est de construire l'interface avocat comme panneau plein écran d'un
ui_extension (pfp.ui.openPanel, pas de nouvelle URL) — ce qui couvre le
bouton mais pas l'exigence explicite d'URL dédiée. Je recommande de traiter
web_app comme un petit chantier plateforme à faire d'abord, avant
d'attaquer le .pfp métier lui-même.

### 9.3 Endpoint par défaut après connexion

Lecture retenue : endpoint par défaut = le dossier (conversation) ou
l'agent sur lequel l'interface avocat s'ouvre automatiquement après
connexion, pas une URL d'API — l'interface dédiée reste un client de la même
instance PawFlow. Mécanisme proposé :

- Une préférence utilisateur côté serveur (pas côté navigateur seul, sinon
  elle ne survit pas au changement d'interface) : default_dossier_id (ou
  default_conversation_id) rattachée au compte utilisateur.
- Un handler serveur du ui_extension (pfp.call("legal.set_default", {...}),
  voir section Server handlers du guide développeur) qui lit/écrit cette
  préférence. Le même handler est appelé :
  - depuis un menu de l'interface PawFlow principale (gear_menu ou
    action_menu du même ui_extension) ;
  - depuis un écran de réglages de l'interface dédiée elle-même (une fois
    web_app disponible, via un appel au même endpoint serveur).
- Au chargement de l'interface dédiée après login, elle lit cette préférence
  et ouvre directement le bon dossier au lieu de la liste des dossiers
  (écran 1 de la section 5 devient l'écran de secours, pas le défaut).

### 9.4 Composition du .pfp

Un seul package, par exemple firm.legal-assistant :

- agent:* — les quatre rôles (assistant / collègue / secrétaire / expert)
  ou un agent unique avec assigned_skills couvrant les quatre postures.
- skill:legal-citations — discipline de citation systématique (garde-fou
  §7.1).
- flow:deadline-watch — CRON quotidien de suivi des délais (§3.3),
  task_def réutilisant manageCalendar + sendEmail.
- service_definition:legal-kb — base juridique indexée (§3.2).
- ui_extension:legal-shell — bouton d'accès + réglage d'endpoint par
  défaut, plus (une fois disponible) l'objet web_app pour la page dédiée
  elle-même.
- Secrets déclarés : credentials manageCalendar/sendEmail, clé du
  provider LLM si non-rétention contractuelle exigée (garde-fou §7.4).

## 10. Conclusion

PawFlow est un bon socle : le gros de l'infrastructure (conversations
persistantes, memory/KG, flows, auth email, et maintenant calendrier) existe
déjà. L'effort réel est l'assemblage métier et surtout la discipline sur les
garde-fous — un cabinet d'avocat ne pardonne pas les approximations que
d'autres domaines tolèrent. Le point qui bloque la livraison en .pfp telle
que demandée n'est pas métier mais plateforme : l'URL dédiée exige un type
d'objet .pfp qui n'existe pas encore (section 9.2) et doit être tranché
avant d'écrire le package lui-même.
