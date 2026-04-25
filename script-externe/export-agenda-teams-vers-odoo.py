#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export des événements Microsoft Teams vers Odoo 19.

Fonctionnement :
  1. Connexion directe à PostgreSQL pour lire les paramètres Azure (client_id,
     tenant_id, client_secret) stockés dans la fiche société Odoo.
  2. Authentification à l'API Microsoft Graph via ClientSecretCredential.
  3. Pour chaque utilisateur Odoo ayant un email correspondant dans Azure AD,
     récupération des événements du calendrier sur les 6 prochains mois via
     calendarView (qui développe les occurrences des événements récurrents).
  4. Création dans Odoo (via XML-RPC) des événements non encore importés,
     identifiés par leur is_teams_event_id (id Graph) et is_teams_ical_uid.

Configuration :
  Copier config.py.example en config.py et renseigner les paramètres.

Lancement :
  python3 export-agenda-teams-vers-odoo.py <nom_base>
  Exemple : python3 export-agenda-teams-vers-odoo.py odoo-agenda19
"""

import logging
import socket
import xmlrpc.client
import ssl
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
import requests
import config

# ─── Forcer IPv4 (IPv6 non routé sur cette VM) ───────────────────────────────
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4(host, port, family=0, *args, **kwargs):
    return _orig_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)
socket.getaddrinfo = _getaddrinfo_ipv4

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
)
_logger = logging.getLogger(__name__)
logging.getLogger('azure').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

# ─── Fenêtre temporelle d'import ─────────────────────────────────────────────
MOIS_AVENIR = 6


def get_teams_config():
    """Lit les paramètres Azure depuis config.py."""
    if not all([config.TEAMS_CLIENT_ID, config.TEAMS_TENANT_ID, config.TEAMS_CLIENT_SECRET]):
        raise RuntimeError(
            "Paramètres Teams manquants dans config.py. "
            "Renseignez TEAMS_CLIENT_ID, TEAMS_TENANT_ID et TEAMS_CLIENT_SECRET."
        )
    return {
        'client_id':     config.TEAMS_CLIENT_ID,
        'tenant_id':     config.TEAMS_TENANT_ID,
        'client_secret': config.TEAMS_CLIENT_SECRET,
    }


def get_odoo_connection(db_name):
    """Ouvre une connexion XML-RPC vers Odoo et retourne (uid, models)."""
    ctx = ssl._create_unverified_context()
    common = xmlrpc.client.ServerProxy(
        '%s/xmlrpc/2/common' % config.ODOO_URL,
        allow_none=True, use_datetime=True, context=ctx
    )
    uid = common.authenticate(db_name, config.ODOO_USERNAME, config.ODOO_PASSWORD, {})
    if not uid:
        raise RuntimeError("Authentification Odoo échouée pour l'utilisateur '%s'" % config.ODOO_USERNAME)
    models = xmlrpc.client.ServerProxy(
        '%s/xmlrpc/2/object' % config.ODOO_URL,
        allow_none=True, use_datetime=True, context=ctx
    )
    _logger.info("Connexion Odoo OK (uid=%s, db=%s)", uid, db_name)
    return uid, models


def get_existing_teams_ids(db_name, uid, models):
    """Retourne l'ensemble des is_teams_event_id déjà importés dans Odoo."""
    lines = models.execute_kw(
        db_name, uid, config.ODOO_PASSWORD,
        'calendar.event', 'search_read',
        [[('is_teams_event_id', '!=', False)]],
        {'fields': ['is_teams_event_id'], 'limit': 100000}
    )
    return {line['is_teams_event_id'] for line in lines}


def get_odoo_users(db_name, uid, models):
    """
    Retourne deux dicts indexés par email :
      - users    : email -> odoo user id
      - partners : email -> [partner_id, partner_name]
    """
    ids = models.execute_kw(
        db_name, uid, config.ODOO_PASSWORD,
        'res.users', 'search',
        [[('active', 'in', [True, False])]],
        {'limit': 10000}
    )
    users = {}
    partners = {}
    for rec in models.execute_kw(
        db_name, uid, config.ODOO_PASSWORD,
        'res.users', 'read', [ids],
        {'fields': ['name', 'email', 'partner_id']}
    ):
        email = (rec.get('email') or '').lower().strip()
        if email:
            users[email]    = rec['id']
            partners[email] = rec['partner_id']
    return users, partners


def get_graph_client(teams_config):
    """Retourne un objet session requests avec le token Azure Bearer."""
    resp = requests.post(
        'https://login.microsoftonline.com/%s/oauth2/v2.0/token' % teams_config['tenant_id'],
        data={
            'grant_type':    'client_credentials',
            'client_id':     teams_config['client_id'],
            'client_secret': teams_config['client_secret'],
            'scope':         'https://graph.microsoft.com/.default',
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()['access_token']
    session = requests.Session()
    session.headers.update({'Authorization': 'Bearer ' + token})
    return session


def get_azure_users(graph_client):
    """Retourne la liste de tous les utilisateurs Azure AD : email -> {id, displayName}."""
    azure_users = {}
    url = "https://graph.microsoft.com/v1.0/users?$select=id,displayName,mail&$top=999"
    while url:
        res = graph_client.get(url)
        data = res.json()
        for u in data.get('value', []):
            mail = (u.get('mail') or '').lower().strip()
            if mail:
                azure_users[mail] = {'id': u['id'], 'displayName': u['displayName']}
        url = data.get('@odata.nextLink')  # pagination
    _logger.info("%d utilisateurs Azure AD récupérés", len(azure_users))
    return azure_users


def get_user_events(graph_client, azure_user_id, date_start, date_end):
    """
    Récupère les événements d'un utilisateur via calendarView.
    calendarView développe les occurrences des événements récurrents,
    contrairement à /events qui retourne uniquement la série principale.
    Gère la pagination via @odata.nextLink.
    """
    events = []
    url = (
        "https://graph.microsoft.com/v1.0/users/%s/calendarView"
        "?startDateTime=%sT00:00:00"
        "&endDateTime=%sT00:00:00"
        "&$select=id,iCalUId,subject,start,end,organizer,attendees,"
        "onlineMeeting,isCancelled,seriesMasterId,type"
        "&$top=100"
    ) % (azure_user_id, date_start, date_end)

    while url:
        res = graph_client.get(url)
        if res.status_code != 200:
            _logger.warning("Erreur Graph pour user %s : %s", azure_user_id, res.status_code)
            break
        data = res.json()
        events.extend(data.get('value', []))
        url = data.get('@odata.nextLink')  # pagination

    return events


def format_datetime(dt_str, tz_str):
    """
    Convertit une datetime Graph (ex: "2026-04-25T09:00:00.0000000")
    en chaîne UTC "YYYY-MM-DD HH:MM:SS" pour Odoo.
    """
    dt_str = dt_str[:19]
    dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
    if tz_str and tz_str.upper() != 'UTC':
        _logger.debug("Timezone non UTC: %s", tz_str)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def create_odoo_event(db_name, uid, models, event, odoo_user_id, partner_id):
    """Crée un événement dans Odoo et retourne l'id créé."""
    participants = [event['organizer']['emailAddress']['name']]
    for att in event.get('attendees', []):
        name = att['emailAddress']['name']
        if name not in participants:
            participants.append(name)

    description = "Participants : %s\n" % ", ".join(participants)
    videocall_url = ''
    if event.get('onlineMeeting'):
        videocall_url = event['onlineMeeting'].get('joinUrl') or ''
        if videocall_url:
            description += "Réunion Teams : %s" % videocall_url

    start = format_datetime(
        event['start']['dateTime'], event['start'].get('timeZone', 'UTC')
    )
    stop = format_datetime(
        event['end']['dateTime'], event['end'].get('timeZone', 'UTC')
    )

    vals = {
        'name'              : event.get('subject') or '(sans titre)',
        'partner_ids'       : [(6, 0, [partner_id[0]])],
        'start'             : start,
        'stop'              : stop,
        'user_id'           : odoo_user_id,
        'description'       : description,
        'is_teams_event_id' : event['id'],
        'is_teams_ical_uid' : event.get('iCalUId', ''),
        'videocall_location': videocall_url,
    }
    new_id = models.execute_kw(db_name, uid, config.ODOO_PASSWORD, 'calendar.event', 'create', [vals])
    return new_id


# ─── Programme principal ──────────────────────────────────────────────────────

def main():
    # Base obligatoire en argument
    db_name = config.ODOO_DB

    # Fenêtre temporelle
    d1 = date.today().strftime("%Y-%m-%d")
    d2 = (date.today() + relativedelta(months=MOIS_AVENIR)).strftime("%Y-%m-%d")
    _logger.info("Fenêtre d'import : %s → %s", d1, d2)

    # Config Azure depuis config.py
    teams_config = get_teams_config()
    _logger.info("Paramètres Azure lus depuis config.py")

    # Connexion Odoo
    uid, models = get_odoo_connection(db_name)

    # Événements déjà importés
    existing_ids = get_existing_teams_ids(db_name, uid, models)
    _logger.info("%d événements Teams déjà présents dans Odoo", len(existing_ids))

    # Utilisateurs Odoo
    odoo_users, odoo_partners = get_odoo_users(db_name, uid, models)
    _logger.info("%d utilisateurs Odoo chargés", len(odoo_users))

    # Client Graph
    _logger.info("Récupération du token Azure...")
    graph_client = get_graph_client(teams_config)
    _logger.info("Token Azure OK")

    # Utilisateurs Azure AD
    azure_users = get_azure_users(graph_client)

    # ── Import par utilisateur ────────────────────────────────────────────────
    total_created = 0
    total_skipped = 0

    for email, azure_info in azure_users.items():
        if email not in odoo_users:
            continue  # utilisateur Azure sans compte Odoo correspondant

        azure_id   = azure_info['id']
        odoo_uid   = odoo_users[email]
        partner_id = odoo_partners[email]

        _logger.info("[%s] Traitement (%s)", azure_info['displayName'], email)

        events = get_user_events(graph_client, azure_id, d1, d2)
        _logger.info("[%s] %d événements récupérés depuis Teams", azure_info['displayName'], len(events))

        for event in events:
            # Ignorer les événements annulés
            if event.get('isCancelled'):
                continue

            event_id = event['id']

            # Déjà importé ?
            if event_id in existing_ids:
                total_skipped += 1
                continue

            try:
                new_id = create_odoo_event(db_name, uid, models, event, odoo_uid, partner_id)
                existing_ids.add(event_id)  # évite les doublons dans la même session
                total_created += 1
                _logger.info(
                    "[%s] + Créé id=%s : %s %s",
                    azure_info['displayName'], new_id, event['start']['dateTime'][:16], event.get('subject', '')
                )
            except Exception:
                _logger.exception(
                    "[%s] ! Erreur création : %s %s",
                    azure_info['displayName'], event['start']['dateTime'][:16], event.get('subject', '')
                )

    _logger.info("Terminé. Créés : %d | Ignorés (déjà présents) : %d", total_created, total_skipped)


if __name__ == '__main__':
    main()
