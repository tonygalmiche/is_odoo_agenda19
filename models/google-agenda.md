# Synchronisation Google Agenda avec Odoo 19

Synchronisez Google Agenda avec Odoo pour voir et gérer les réunions depuis les deux plateformes (synchronisation bidirectionnelle).

---

## Étape 1 — Configuration dans Google Console

### 1.1 Créer un projet API

1. Aller sur [Google API Console](https://console.developers.google.com/) et se connecter.
2. Cliquer sur **Sélectionner un projet** → **Nouveau projet**.
3. Nommer le projet (ex. `Odoo Sync`) et cliquer sur **Créer**.

### 1.2 Activer l'API Google Calendar

1. Dans le menu gauche, cliquer sur **API et services activés**.
2. Rechercher `Google Calendar API` dans la barre de recherche.
3. Sélectionner **Google Calendar API** et cliquer sur **Activer**.

### 1.3 Configurer le Branding

1. Cliquer sur **Branding** dans le menu gauche.
2. Saisir `Odoo` comme nom d'application et renseigner votre email de support.
3. Dans **Page d'accueil de l'application**, saisir l'URL de votre Odoo (ex. `https://votre-domaine-odoo.com`).
4. Dans **Domaines autorisés**, ajouter votre domaine (ex. `votre-domaine-odoo.com`).
5. Cliquer sur **Enregistrer**.

### 1.4 Configurer l'Audience

1. Cliquer sur **Audience** dans le menu gauche.
2. Vérifier que le **Type d'utilisateur** est **Externe**.
3. Si l'état est **En production**, cliquer sur **Revenir au mode test** — plus sécurisé pour un nombre limité d'utilisateurs connus.
4. Dans la section **Test users**, cliquer sur **Add users**, ajouter les adresses email des utilisateurs Odoo concernés et cliquer sur **Enregistrer**.

> **Note** : En mode test, seuls les emails listés peuvent se connecter. Sans utilisateur de test, vous obtiendrez une `Error 403` lors de la synchronisation.

### 1.5 Créer le client OAuth

1. Cliquer sur le **menu hamburger** (☰) en haut à gauche.
2. Aller dans **API et services** → **Identifiants**.
3. Cliquer sur **+ Créer des identifiants** → **ID client OAuth 2.0**.
3. Définir le **Type d'application** : **Application Web**.
4. Nommer le client (ex. `Ma base de données Odoo`).
4. Dans **Origines JavaScript autorisées**, cliquer sur **+ Ajouter un URI** et saisir :
   ```
   https://votre-odoo.com
   ```
5. Dans **URIs de redirection autorisés**, cliquer sur **+ Ajouter un URI** et saisir :
   ```
   https://votre-odoo.com/google_account/authentication
   ```
5. Cliquer sur **Créer**.
6. **Conserver précieusement** le **Client ID** et le **Client Secret** affichés.

> **Important** : L'URL doit correspondre exactement au paramètre système `web.base.url` d'Odoo (vérifiable via *Paramètres → Technique → Paramètres système* en mode développeur).

---

## Étape 2 — Configuration dans Odoo

1. Aller dans **Paramètres → Calendrier**.
2. Cocher la case **Google Agenda**.
3. Coller le **Client ID** et le **Client Secret** obtenus à l'étape précédente.
4. Cliquer sur **Enregistrer**.

> **Astuce** : La case **Pause Synchronization** permet de suspendre temporairement la synchronisation pour les tests sans supprimer les identifiants.

---

## Étape 3 — Synchroniser le calendrier (par utilisateur)

Chaque utilisateur doit effectuer cette étape **une seule fois** depuis son propre compte Odoo.

1. Ouvrir l'application **Calendrier** dans Odoo.
2. Cliquer sur le bouton **Synchroniser avec Google**.
3. Lors de la redirection vers Google :
   - Sélectionner le compte Google à utiliser.
   - Cliquer sur **Continuer** (même si l'application affiche un avertissement non vérifiée).
   - Autoriser le transfert de données.

La synchronisation est ensuite automatique et bidirectionnelle.

---

## Comportement de la synchronisation

| Action dans Odoo | Effet dans Google |
|---|---|
| Créer un événement | Invitation envoyée à tous les participants |
| Supprimer un événement | Annulation envoyée à tous les participants |
| Ajouter un contact à un événement | Invitation envoyée au nouveau participant |
| Retirer un contact d'un événement | Annulation envoyée au participant retiré |

> Pour créer un événement Google sans envoyer de notification, sélectionner **Don't Send** lors de l'invite.

---

## Dépannage

### Réinitialiser la synchronisation

1. Aller dans **Paramètres → Gérer les utilisateurs**.
2. Sélectionner l'utilisateur concerné → onglet **Calendrier**.
3. Cliquer sur **Reset Account** sous Google Calendar.

**Options disponibles lors de la réinitialisation :**

- *Événements existants* : laisser intacts / supprimer de Google / supprimer d'Odoo / supprimer des deux
- *Prochaine synchronisation* : nouveaux événements uniquement / tous les événements existants

### Erreurs fréquentes

| Erreur | Cause | Solution |
|---|---|---|
| `Error 403: access_denied` | Aucun utilisateur de test configuré | Ajouter l'email dans *Audience → Test users* |
| `Error 400: redirect_uri_mismatch` | Type d'application incorrect (Desktop App) | Recréer les identifiants avec le type **Application Web** |
| Avertissement OAuth (100 logins) | Statut de publication en Production | Repasser en mode **Testing** dans la console Google |

---

## Références

- [Documentation officielle Odoo 19](https://www.odoo.com/documentation/19.0/fr/applications/productivity/calendar/google.html)
- [Google API Console](https://console.developers.google.com/)

---

## Problèmes connus

### Erreur "Vous devez choisir au moins un jour dans la semaine"

**Symptôme** dans les logs :
```
ERROR odoo.addons.google_calendar.models.res_users: Calendar Synchro - Exception : Vous devez choisir au moins un jour dans la semaine!
odoo.exceptions.UserError: Vous devez choisir au moins un jour dans la semaine
```

**Cause** : Des anciennes récurrences hebdomadaires en base ont les champs `mon/tue/wed/thu/fri/sat/sun` à `NULL` (données corrompues issues d'une ancienne version d'Odoo). Lors de la synchro, Odoo tente de construire la règle rrule pour Google et échoue.

**Diagnostic** :
```sql
SELECT id, name, rrule_type, need_sync, mon, tue, wed, thu, fri, sat, sun
FROM calendar_recurrence
WHERE need_sync = true AND rrule_type = 'weekly' AND mon IS NULL;
```

**Correction** : Désactiver `need_sync` sur ces récurrences corrompues (les événements restent en base, ils ne sont plus envoyés à Google) :
```sql
UPDATE calendar_recurrence
SET need_sync = false
WHERE need_sync = true AND rrule_type = 'weekly' AND mon IS NULL;
```

Relancer ensuite le cron depuis **Paramètres → Technique → Actions planifiées → Google Calendar: synchronization → Lancer manuellement**.

---

### Erreur persistante — Champs jours NULL sur récurrences avec BYDAY dans la rrule (2026-05-01)

**Symptôme** : la même erreur "Vous devez choisir au moins un jour dans la semaine" persiste après le premier correctif.

**Cause** : Le premier correctif visait uniquement les récurrences avec `need_sync = true`. Il existait également des milliers de récurrences importées depuis Teams avec `google_id IS NULL + active = true`, dont les champs jours (`mon`, `tue`, etc.) sont tous à `NULL`, mais dont le champ `rrule` contient bien un `BYDAY=MO/TU/etc.`. Odoo les inclut automatiquement dans la synchro (car `google_id IS NULL AND active = true`) et plante sur la reconstruction de la rrule.

**Diagnostic** :
```sql
-- Compter les récurrences weekly sans aucun jour valide (toutes valeurs NULL ou false)
SELECT COUNT(*), google_id IS NULL AS no_gid, active
FROM calendar_recurrence
WHERE rrule_type = 'weekly'
  AND COALESCE(mon,false)=false AND COALESCE(tue,false)=false
  AND COALESCE(wed,false)=false AND COALESCE(thu,false)=false
  AND COALESCE(fri,false)=false AND COALESCE(sat,false)=false
  AND COALESCE(sun,false)=false
GROUP BY google_id IS NULL, active;
```

**Correction** : Reconstruire les champs jours depuis le `BYDAY` présent dans le champ `rrule` :
```sql
UPDATE calendar_recurrence SET
  mon = CASE WHEN rrule ~ 'BYDAY=[A-Z,]*MO' THEN true ELSE false END,
  tue = CASE WHEN rrule ~ 'BYDAY=[A-Z,]*TU' THEN true ELSE false END,
  wed = CASE WHEN rrule ~ 'BYDAY=[A-Z,]*WE' THEN true ELSE false END,
  thu = CASE WHEN rrule ~ 'BYDAY=[A-Z,]*TH' THEN true ELSE false END,
  fri = CASE WHEN rrule ~ 'BYDAY=[A-Z,]*FR' THEN true ELSE false END,
  sat = CASE WHEN rrule ~ 'BYDAY=[A-Z,]*SA' THEN true ELSE false END,
  sun = CASE WHEN rrule ~ 'BYDAY=[A-Z,]*SU' THEN true ELSE false END
WHERE rrule_type = 'weekly'
  AND COALESCE(mon,false)=false AND COALESCE(tue,false)=false
  AND COALESCE(wed,false)=false AND COALESCE(thu,false)=false
  AND COALESCE(fri,false)=false AND COALESCE(sat,false)=false
  AND COALESCE(sun,false)=false
  AND rrule ~ 'BYDAY=';
```

→ Résultat : **1952 lignes mises à jour**.

**Vérification** : s'assurer qu'il ne reste aucune récurrence weekly sans jours valides impliquées dans la synchro :
```sql
SELECT COUNT(*) AS reste_sans_jours
FROM calendar_recurrence
WHERE rrule_type = 'weekly'
  AND COALESCE(mon,false)=false AND COALESCE(tue,false)=false
  AND COALESCE(wed,false)=false AND COALESCE(thu,false)=false
  AND COALESCE(fri,false)=false AND COALESCE(sat,false)=false
  AND COALESCE(sun,false)=false;
```

Le seul record restant (id=249) a `need_sync=false` et un `google_id` valide : il n'est pas dans la queue de synchro et ne cause pas d'erreur.

> ⚠️ **Effet de bord** : les récurrences corrigées avec `google_id IS NULL + active = true` seront tentées de synchronisation vers Google (200 par run de cron). Il s'agit d'anciens événements Teams (2021+). Si ces événements indésirables apparaissent dans Google Calendar, il faudra archiver les récurrences expirées (champ `until` < aujourd'hui).

---

## Conseils d'utilisation

### Désactiver les alertes e-mail de Google Calendar (doublons avec Odoo)

Odoo envoie déjà ses propres notifications par e-mail. Pour éviter de recevoir en double les alertes de Google Calendar :

1. Aller sur [calendar.google.com](https://calendar.google.com)
2. Cliquer sur **⚙️ Paramètres** (en haut à droite)
3. Dans le menu gauche, **Paramètres généraux** → **Notifications par e-mail** → décocher **Notifications par e-mail d'autres calendriers**

Pour chaque calendrier individuellement :
1. Dans le menu gauche, cliquer sur **⋮** à côté du calendrier → **Paramètres et partage**
2. Section **Autres notifications** → mettre tous les champs sur **Aucun**
3. Section **Notifications d'événements** → supprimer toutes les notifications par e-mail (conserver uniquement les notifications dans l'application si souhaité)

---

## Stockage des tokens Google en base de données

Les informations de connexion Google Agenda d'un utilisateur ne sont **pas** stockées directement dans la table `res_users`, mais dans le modèle **`res.users.settings`** (table `res_users_settings`).

Les champs sur `res.users` ne sont que des champs `related` (lecture seule) pointant vers `res_users_settings_id` :

| Champ | Description |
|---|---|
| `google_calendar_rtoken` | Refresh token OAuth2 (visible en mode admin uniquement) |
| `google_calendar_token` | Access token OAuth2 (jeton d'accès courant) |
| `google_calendar_token_validity` | Date d'expiration de l'access token |
| `google_calendar_sync_token` | Token de synchronisation incrémentale Google |
| `google_calendar_cal_id` | Identifiant du calendrier Google de l'utilisateur |
| `google_synchronization_stopped` | `true` si l'utilisateur a arrêté manuellement la synchro |

Pour consulter ces valeurs directement en SQL :

```sql
SELECT user_id, google_calendar_rtoken, google_calendar_token, google_calendar_token_validity,
       google_calendar_sync_token, google_calendar_cal_id, google_synchronization_stopped
FROM res_users_settings
WHERE user_id = <id_utilisateur>;
```

---

## Synchronisation Google Agenda pour les participants non-créateurs

### Problème d'origine

La synchronisation native d'Odoo ne fonctionne que depuis la perspective du **créateur** d'un événement (`calendar_event.user_id`). Lorsqu'un administrateur crée un rendez-vous en y ajoutant un autre utilisateur (ex. Tony), Odoo stocke le `google_id` de l'événement dans le calendrier Google **d'Admin**, pas dans celui de Tony.

Tenter un `patch` Google avec cet ID depuis le compte Tony échoue silencieusement car l'événement n'existe pas dans son calendrier.

### Solution implémentée (module `is_odoo_agenda19`)

#### Nouveau champ : `is_google_event_id` sur `calendar.attendee`

Chaque participant dispose maintenant de son propre identifiant Google (`is_google_event_id`). Il est distinct du `google_id` de l'événement (qui appartient au créateur).

```
calendar.attendee
├── is_user_id          (Many2one res.users, calculé depuis partner_id)
└── is_google_event_id  (Char, ID de l'événement dans le Google Agenda du participant)
```

#### Logique de `synchroniser_google_user(event, user, attendee)`

| Condition | Action Google |
|---|---|
| `user` sans `google_calendar_rtoken` ou synchro arrêtée | Rien (sortie immédiate) |
| `user` = créateur de l'événement | `PATCH` avec `event.google_id` (comportement d'origine) |
| Participant, état = `declined` + `is_google_event_id` connu | `DELETE` dans son agenda, efface `is_google_event_id` |
| Participant, `is_google_event_id` inconnu | `INSERT` dans son agenda, stocke l'ID retourné dans `is_google_event_id` |
| Participant, `is_google_event_id` déjà connu | `PATCH` avec son `is_google_event_id` |

Les appels `INSERT`/`PATCH` participants sont faits avec `send_updates=False` pour ne pas envoyer d'invitations Google.

#### Déclenchement de la synchronisation

La fonction est appelée depuis trois points :

1. **`CalendarAttendee.create()`** — quand un participant est ajouté à un événement (création batch gérée avec `@api.model_create_multi`)
2. **`CalendarAttendee.write()`** — quand l'état d'un participant change (ex. accepté → refusé), sauf si `skip_google_sync=True` dans le contexte
3. **`CalendarEvent.write()`** — quand un champ métier de l'événement est modifié (`name`, `start`, `stop`, `duration`, `description`, `location`, `allday`)

#### Protection anti-récursion

Quand `synchroniser_google_user` écrit l'`is_google_event_id` retourné par Google dans l'attendee, il utilise `with_context(skip_google_sync=True)` pour éviter que ce `write()` ne redéclenche une nouvelle synchro.

#### Filtre sur les utilisateurs concernés

Seuls les utilisateurs ayant **activé** la synchronisation Google Agenda sont traités :

```python
if not user_sudo.google_calendar_rtoken or user_sudo.google_synchronization_stopped:
    return
```

Cela évite des appels API inutiles pour les ~90 utilisateurs sans compte Google connecté.
