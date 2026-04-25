# export-agenda-teams-vers-odoo.py

Script Python permettant d'importer automatiquement les événements du calendrier
Microsoft Teams (via l'API Microsoft Graph) dans le calendrier Odoo 19.

---

## Fonctionnement

1. **Lecture des paramètres** : le nom de la base PostgreSQL, les paramètres de
   connexion Odoo (XML-RPC) et
   les clés Azure sont lus depuis le fichier `config.py` (non versé sur Git).

3. **Récupération des utilisateurs** : le script croise les utilisateurs Azure AD
   avec les utilisateurs Odoo (par email). Seuls les utilisateurs présents dans
   les deux systèmes sont traités.

4. **Import des événements** : pour chaque utilisateur, les événements sont
   récupérés via `calendarView` sur les **6 prochains mois**.
   > `calendarView` est utilisé (et non `/events`) car il **développe les
   > occurrences des événements récurrents**, ce qui évite de n'importer que
   > la série principale.

5. **Déduplication** : un événement déjà importé (identifié par `is_teams_event_id`)
   n'est jamais recréé.

6. **Création dans Odoo** : les événements sont créés via XML-RPC avec les champs
   `is_teams_event_id`, `is_teams_ical_uid`, participants, lien Teams, etc.

---

## Prérequis

### Dépendances Python

Le dossier `script-externe/` est sur un partage VirtualBox (vboxsf) qui ne supporte
pas les symlinks. Le venv doit donc être créé sur le filesystem natif de la VM :

```bash
apt install python3.13-venv   # si pas encore installé
python3 -m venv ~/venv-odoo-agenda
~/venv-odoo-agenda/bin/pip install -r requirements.txt
```


### Fichier de configuration

Créer un fichier `config.py` à côté du script (non versé sur Git) :
```python
# Connexion Odoo XML-RPC
ODOO_URL      = "https://odoo-agenda.com"
ODOO_DB       = "odoo-agenda19"
ODOO_USERNAME = "admin"
ODOO_PASSWORD = "..."

# Microsoft Teams (récupérés depuis le portail Azure)
TEAMS_CLIENT_ID     = "..."
TEAMS_TENANT_ID     = "..."
TEAMS_CLIENT_SECRET = "..."
```

---

## Configuration de l'App Registration Azure (clés API Teams)

### Étape 1 — Créer l'App Registration

1. Aller sur [https://portal.azure.com](https://portal.azure.com)
2. Menu **Microsoft Entra ID**
3. Dans le menu de gauche, dérouler **Gérer** → **Inscriptions d'applications**
4. Cliquer **+ Nouvelle inscription**
5. Remplir le formulaire :
   - **Nom** : `Odoo 19 Agenda Import`
   - **Types de comptes pris en charge** : `Locataire unique seulement : PLASTIGRAY`
   - **URI de redirection** : laisser vide
6. Cliquer **S'inscrire**

### Étape 2 — Récupérer les identifiants

Sur la page de l'application créée :
- **Application (client) ID** → copier dans le champ `Client ID` de la fiche société Odoo
- **Directory (tenant) ID** → copier dans le champ `Tenant ID`

### Étape 3 — Créer le Client Secret

1. Dans le menu de gauche, dérouler **Gérer** → **Certificats & secrets** → onglet **Secrets client** → **+ Nouveau secret client**
2. Description : `odoo-agenda`
3. Expires : choisir la durée (ex: 24 mois)
4. Cliquer **Add**
5. **Copier immédiatement la valeur** (elle ne sera plus visible après)
6. Coller dans le champ `Client Secret` de la fiche société Odoo

### Étape 4 — Ajouter les permissions API

1. Dans le menu de gauche, dérouler **Gérer** → **API autorisées** → **+ Ajouter une autorisation**
2. **Microsoft Graph** → **Autorisations d'application**
3. Ajouter les permissions suivantes :
   - `Calendars.Read`
   - `User.Read.All`
4. Cliquer **Ajouter des autorisations**
5. La liste affiche maintenant 3 entrées :
   - `Calendars.Read` (Application) ← ajoutée
   - `User.Read` (Déléguée) ← présente par défaut, ne pas supprimer
   - `User.Read.All` (Application) ← ajoutée
6. Cliquer **Accorder un consentement d'administrateur pour PLASTIGRAY** ← important !
   > Sans cette étape, `Calendars.Read` et `User.Read.All` restent avec le statut
   > "Pas accordé" et le script ne fonctionnera pas.

---

## Lancement manuel (développement / VM)

```bash
cd is_odoo_agenda19/script-externe
~/venv-odoo-agenda/bin/python3 export-agenda-teams-vers-odoo.py
```

## Lancement automatique (cron, serveur de production)

Pour lancer le script toutes les heures via cron (compte `odoo`) :
```bash
crontab -e
```
Ajouter (adapter le chemin selon l'emplacement des addons) :
```
0 * * * * ~/venv-odoo-agenda/bin/python3 is_odoo_agenda19/script-externe/export-agenda-teams-vers-odoo.py >> /var/log/odoo/teams-import.log 2>&1
```

---

## Champs Odoo utilisés

| Champ                  | Modèle          | Description                        |
|------------------------|-----------------|------------------------------------|
| `is_teams_event_id`    | calendar.event  | ID Graph de l'événement (dédup)    |
| `is_teams_ical_uid`    | calendar.event  | iCalUId de l'événement             |

---

## Problèmes connus

### IPv6 bloque les connexions vers Azure

**Symptôme** : le script se bloque silencieusement après "X utilisateurs Odoo chargés",
sans erreur ni timeout, à l'étape de récupération du token Azure.

**Cause** : Python `requests` tente en priorité les adresses IPv6 de
`login.microsoftonline.com`. Sur cette VM (VirtualBox), IPv6 n'est pas routé vers
internet, ce qui provoque une attente indéfinie. `curl` n'est pas affecté car il
tente IPv4 et IPv6 en parallèle et prend le premier qui répond.

**Solution appliquée** : patch de `socket.getaddrinfo` au démarrage du script pour
forcer IPv4 :
```python
import socket
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4(host, port, family=0, *args, **kwargs):
    return _orig_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)
socket.getaddrinfo = _getaddrinfo_ipv4
```

**Solution alternative** (système) : désactiver IPv6 sur la VM :
```bash
echo 'net.ipv6.conf.all.disable_ipv6 = 1' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```
