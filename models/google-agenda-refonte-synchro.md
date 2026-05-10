# Refonte de la synchronisation Google Agenda — module `is_odoo_agenda19`

**Date :** 2026-05-09  
**Version module :** 19.0.1.0.3  
**Auteur :** InfoSaône / Tony Galmiche

---

## 1. Pourquoi la synchro native Odoo/Google ne fonctionne pas ici

La synchronisation native d'Odoo a été conçue pour **Google Workspace** (domaine d'entreprise géré par Google), qui permet la *Domain-Wide Delegation* : un compte de service peut agir au nom de tous les utilisateurs du domaine sans autorisation individuelle.

Chez Plastigray, chaque utilisateur possède un **compte Google personnel** (domaine non géré par Google). Cela implique :

- **Pas de délégation de domaine** : impossible d'agir sur le calendrier d'un autre utilisateur sans son token OAuth propre.
- **`google_id` non partageable** : l'ID retourné par Google lors d'un `INSERT` est propre au calendrier du créateur. Utiliser cet ID avec le token d'un autre utilisateur retourne une erreur 404.
- **Modèle "attendees" Google = invitation e-mail uniquement** : ajouter des participants via l'API ne crée l'événement dans leur agenda que s'ils acceptent manuellement. Aucun moyen d'imposer l'événement sans leur accord (protection vie privée Google).
- **Un token OAuth par utilisateur** : il n'existe pas de super-token d'administration. Chaque appel API doit être fait avec le token de l'utilisateur concerné.
- **Quotas API plus faibles** sur les comptes personnels → erreurs `403 rateLimitExceeded` lors des synchros en masse.

| | Google Workspace | Comptes personnels (Plastigray) |
|---|---|---|
| Délégation domain-wide | ✅ | ❌ |
| Créer un event dans l'agenda d'un autre | ✅ via service account | ❌ invitation e-mail seulement |
| `google_id` partageable | ✅ | ❌ ID propre à chaque calendrier |
| Quotas API | ✅ élevés | ⚠️ limités |

C'est la raison fondamentale pour laquelle le module a été entièrement refondu : plutôt que de s'appuyer sur le modèle "invitation", il crée un événement **indépendant** dans le Google Agenda de **chaque participant** via **son propre token OAuth**, et stocke un `is_google_event_id` distinct par participant dans `calendar.attendee`.

---

## 2. Problème d'origine — Limitation native d'Odoo 19

La synchronisation Google Calendar native d'Odoo ne fonctionne que du point de vue du **créateur** de l'événement (`calendar_event.user_id`). Odoo stocke un seul `google_id` par événement, correspondant à l'entrée dans le Google Agenda du créateur.

**Conséquence :** lorsqu'un administrateur crée un rendez-vous en ajoutant d'autres utilisateurs comme participants (ex. Tony), l'événement est poussé uniquement dans l'agenda Google de l'admin. Tony ne voit rien dans son Google Agenda. Tenter un `PATCH` avec le `google_id` de l'admin depuis le token de Tony échoue silencieusement (l'événement n'existe pas dans son calendrier).

De plus, la synchronisation native est **bidirectionnelle** (Google → Odoo + Odoo → Google), ce qui crée des conflits et des doublons lors d'imports en masse (ex. Teams).

---

## 2. Solution mise en place

### 2.1 Nouveau champ : `is_google_event_id` sur `calendar.attendee`

Chaque participant dispose de **son propre identifiant Google** stocké dans `calendar.attendee.is_google_event_id`. Ce champ est distinct du `google_id` de l'événement (qui appartient au créateur).

```
calendar.attendee
├── is_user_id          (Many2one res.users, calculé depuis partner_id, stocké)
└── is_google_event_id  (Char, ID de l'événement dans le Google Agenda du participant)
```

### 2.2 Sens unique : Odoo → Google uniquement

Les méthodes natives `_sync_google2odoo()` et `_sync_odoo2google()` sont surchargées pour ne rien faire (retour immédiat avec log). Tout passe par `synchroniser_google_user()`.

### 2.3 Fonction centrale : `synchroniser_google_user(event, user, attendee)`

| Condition | Action Google |
|---|---|
| Pas de `google_calendar_rtoken` ou synchro arrêtée | Rien |
| Événement archivé (`active=False`) | Rien |
| Hors fenêtre de synchro (J-14 / J+365) | Rien |
| Participant `declined` + `is_google_event_id` connu | `DELETE` + efface le champ |
| `is_google_event_id` inconnu | `INSERT` + stocke l'ID retourné |
| `is_google_event_id` déjà connu | `PATCH` |

Les appels sont faits avec `send_updates=False` (pas d'invitations Google). La liste des participants Odoo est injectée dans la description de l'événement Google.

### 2.4 Déclencheurs de synchronisation

- **`CalendarAttendee.create()`** — ajout d'un participant (géré avec `@api.model_create_multi`)
- **`CalendarAttendee.write()`** — changement d'état (accepté/refusé), sauf `skip_google_sync=True`
- **`CalendarEvent.write()`** — modification d'un champ métier : `name`, `start`, `stop`, `duration`, `description`, `location`, `allday`, `partner_ids`, `attendee_ids`
- **`CalendarEvent.unlink()`** — suppression définitive → DELETE dans Google avant `super()`

### 2.5 Protection anti-récursion et anti-boucle

- `with_context(skip_google_sync=True)` : utilisé lors de l'écriture de `is_google_event_id` par le module lui-même, évite que ce `write()` ne redéclenche une synchro.
- `with_context(dont_notify=True)` : positionné par le cron natif Google → Odoo, bloque tout renvoi vers Google.
- `_google_call_with_retry()` : retry automatique sur erreur `403 rateLimitExceeded` (backoff exponentiel 1s/2s), ignore silencieusement les `410 Gone`.

---

## 3. Problèmes de données rencontrés en production

### 3.1 Récurrences hebdomadaires avec champs jours NULL (2026-04)

**Symptôme :** `UserError: Vous devez choisir au moins un jour dans la semaine` dans les logs lors du cron de synchro.

**Cause :** Des récurrences `rrule_type = 'weekly'` avaient les colonnes `mon/tue/wed/thu/fri/sat/sun` à `NULL` en base (données corrompues issues d'une ancienne version ou d'un import Teams). Odoo échoue lors de la reconstruction de la rrule pour Google.

**Correction 1 :** désactiver `need_sync` sur les récurrences `need_sync = true` sans aucun jour valide.

**Correction 2 (2026-05-01) :** 1952 récurrences supplémentaires avaient `google_id IS NULL + active = true` — automatiquement incluses dans la synchro — avec `rrule` contenant `BYDAY=MO/TU/…` mais champs jours à NULL. Correction : reconstruire les champs jours depuis le `BYDAY` présent dans `rrule` via SQL `UPDATE`.

---

## 4. Bugs rencontrés et corrigés le 2026-05-10

### 4.1 Doublons dans Google lors de la création d'un événement récurrent

**Cause :** `google_sync.create()` appelle `_google_insert()` directement (contourne `_sync_odoo2google()`), et `CalendarRecurrence` insère en plus un événement récurrent natif.

**Correction :** Forcer `need_sync=False` dans `CalendarEvent.create()` et `CalendarRecurrence.create()` avant `super()`.

---

### 4.2 "Supprimer tous les événements de la série" ne supprime que le premier

**Cause :** `action_unlink_event()` natif court-circuite la logique de série quand `_has_any_active_synchronization()` retourne `True` : il appelle `self.unlink()` sur l'événement courant uniquement.

**Correction :** Surcharge d'`action_unlink_event()` pour intercepter `recurrence='all'`/`'next'` et appeler directement `action_mass_deletion()`.

---

### 4.3 Après modification de tous les événements, le premier disparaît du Google Agenda

**Cause :** Odoo archive tous les events (`active=False`) → notre code les supprime de Google mais **oubliait de vider `is_google_event_id`**. Ensuite Odoo réactive le base event ; `synchroniser_google_user()` voyait un ID non vide → tentait un `PATCH` sur un ID supprimé → erreur `410 Gone` silencieuse → event jamais inséré.

**Correction :** Dans le bloc `active=False`, vider `is_google_event_id` dans un `finally` après le DELETE. Ajouter un bloc `active=True` qui déclenche `synchroniser_google_user()` → le base event réactivé est correctement inséré.

---

### 4.4 Rappels Odoo silencieux quand la synchro Google est active

**Cause :** `google_calendar` surcharge `_get_notify_alert_extra_conditions()` en ajoutant `AND event.google_id IS NULL`. Le cron de rappels Odoo ignore donc tous les événements ayant un `google_id`, car Google est censé les envoyer à leur place. Comme notre module ne délègue pas les rappels à Google, ils étaient perdus.

**Correction :** Surcharge de `_get_notify_alert_extra_conditions()` dans notre module pour retourner `SQL("")` (aucun filtre), restaurant le comportement normal pour tous les événements.

---

### 4.5 Erreur `eventRemindersCountExceedsLimit` lors du PATCH/INSERT Google

**Cause :** La méthode native `_google_values()` inclut toujours la clé `reminders` (liste des `alarm_ids` Odoo). Google rejette la requête avec une erreur 400 si le nombre de rappels dépasse sa limite.

**Correction :** Supprimer la clé `reminders` du payload avant l'envoi à Google (`values.pop('reminders', None)`), de la même façon que `attendees` et `id`. Les rappels restent gérés exclusivement par Odoo.

---

## 5. Outils de maintenance (`res.users`)

Trois actions manuelles disponibles sur la fiche utilisateur (mode admin) :

| Action | Rôle |
|---|---|
| `action_synchroniser_google` | Pousse tous les événements de la fenêtre vers Google (sans suppression préalable) |
| `action_supprimer_google_events` | Liste les événements Google de l'utilisateur, supprime ceux dont le titre correspond à un événement Odoo, conserve les événements créés manuellement dans Google |
| `action_supprimer_doublons_odoo` | Détecte et archive les doublons Odoo sur la fenêtre (même `name+start+stop`), priorité aux événements avec `google_id` |

---

## 5. Autres fonctionnalités du module

- **Détection des conflits horaires** (`is_alerte`) : affiche en temps réel les participants déjà occupés sur le même créneau.
- **Affichage coloré des participants** (`is_participants`) : vert=accepté, rouge=refusé, gris=en attente.
- **Couleur créateur** (`is_creator_calendar_color`) : colorisation des événements dans la vue calendrier selon le partenaire créateur.
- **Mail de refus** : envoi automatique d'un e-mail ICS au créateur quand un participant refuse l'invitation.
- **Remise à `needsAction`** : quand la date d'un événement change, les participants ayant déjà répondu sont replacés en attente de confirmation.
- **Champs Teams** : `is_teams_event_id` et `is_teams_ical_uid` pour tracer l'origine des événements importés depuis Microsoft Teams.
