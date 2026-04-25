#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Export des événements Microsoft Teams vers Odoo 19 — avec mise à jour.

Identique à export-agenda-teams-vers-odoo.py, mais gère aussi la mise à jour
des événements déjà importés si leurs données ont changé dans Teams :
  - Titre (name)
  - Date/heure début et fin (start, stop)
  - Description (participants, lien Teams)
  - Lien visioconférence (videocall_location)

Les champs non écrasés (même si différents) : user_id, partner_ids.
"""

import logging
import socket
import xmlrpc.client
import ssl
import argparse
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


def get_existing_teams_events(db_name, uid, models):
    """
    Retourne un dict : teams_event_id -> {odoo_id, name, start, stop, description, videocall_location}
    pour tous les événements déjà importés.
    """
    lines = models.execute_kw(
        db_name, uid, config.ODOO_PASSWORD,
        'calendar.event', 'search_read',
        [[('is_teams_event_id', '!=', False)]],
        {
            'fields': ['id', 'is_teams_event_id', 'name', 'start', 'stop',
                       'description', 'videocall_location'],
            'limit': 100000,
        }
    )
    result = {}
    for line in lines:
        teams_id = line['is_teams_event_id']
        # start/stop sont retournés comme datetime ou str selon le proxy
        start = line['start']
        stop  = line['stop']
        if isinstance(start, datetime):
            start = start.strftime('%Y-%m-%d %H:%M:%S')
        if isinstance(stop, datetime):
            stop = stop.strftime('%Y-%m-%d %H:%M:%S')
        result[teams_id] = {
            'odoo_id'           : line['id'],
            'name'              : line['name'] or '',
            'start'             : start,
            'stop'              : stop,
            'description'       : line['description'] or '',
            'videocall_location': line['videocall_location'] or '',
        }
    return result


def get_odoo_users(db_name, uid, models):
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
    azure_users = {}
    url = "https://graph.microsoft.com/v1.0/users?$select=id,displayName,mail&$top=999"
    while url:
        res = graph_client.get(url)
        data = res.json()
        for u in data.get('value', []):
            mail = (u.get('mail') or '').lower().strip()
            if mail:
                azure_users[mail] = {'id': u['id'], 'displayName': u['displayName']}
        url = data.get('@odata.nextLink')
    _logger.info("%d utilisateurs Azure AD récupérés", len(azure_users))
    return azure_users


def get_user_events(graph_client, azure_user_id, date_start, date_end):
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
        url = data.get('@odata.nextLink')

    return events


def format_datetime(dt_str, tz_str):
    dt_str = dt_str[:19]
    dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")
    if tz_str and tz_str.upper() != 'UTC':
        _logger.debug("Timezone non UTC: %s", tz_str)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def build_event_vals(event, odoo_user_id, partner_id):
    """Construit le dict de valeurs à partir d'un événement Teams."""
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

    start = format_datetime(event['start']['dateTime'], event['start'].get('timeZone', 'UTC'))
    stop  = format_datetime(event['end']['dateTime'],   event['end'].get('timeZone', 'UTC'))

    return {
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


# Champs comparés pour détecter une mise à jour nécessaire
UPDATE_FIELDS = ('name', 'start', 'stop', 'videocall_location')


def compute_update_vals(new_vals, existing):
    """Retourne uniquement les champs qui ont changé (hors partner_ids/user_id)."""
    diff = {}
    for field in UPDATE_FIELDS:
        new_val = (new_vals.get(field) or '').strip()
        old_val = (existing.get(field) or '').strip()
        if new_val != old_val:
            diff[field] = new_vals[field]
    return diff


# ─── Programme principal ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--filtre-utilisateur', '-f',
        default='',
        help="Traiter uniquement les utilisateurs dont le nom contient ce texte (ex: 'Jean BARRES')"
    )
    args = parser.parse_args()
    filtre = args.filtre_utilisateur.lower().strip()
    if filtre:
        _logger.info("Filtre utilisateur actif : '%s'", filtre)

    db_name = config.ODOO_DB

    d1 = date.today().strftime("%Y-%m-%d")
    d2 = (date.today() + relativedelta(months=MOIS_AVENIR)).strftime("%Y-%m-%d")
    _logger.info("Fenêtre d'import : %s → %s", d1, d2)

    teams_config = get_teams_config()
    _logger.info("Paramètres Azure lus depuis config.py")

    uid, models = get_odoo_connection(db_name)

    existing_events = get_existing_teams_events(db_name, uid, models)
    _logger.info("%d événements Teams déjà présents dans Odoo", len(existing_events))

    odoo_users, odoo_partners = get_odoo_users(db_name, uid, models)
    _logger.info("%d utilisateurs Odoo chargés", len(odoo_users))

    _logger.info("Récupération du token Azure...")
    graph_client = get_graph_client(teams_config)
    _logger.info("Token Azure OK")

    azure_users = get_azure_users(graph_client)

    total_created = 0
    total_updated = 0
    total_skipped = 0
    total_deleted = 0

    for email, azure_info in azure_users.items():
        if email not in odoo_users:
            continue

        azure_id   = azure_info['id']
        odoo_uid   = odoo_users[email]
        partner_id = odoo_partners[email]
        display    = azure_info['displayName']
        if filtre and filtre not in display.lower():
            continue
        _logger.info("[%s] Traitement (%s)", display, email)

        events = get_user_events(graph_client, azure_id, d1, d2)
        _logger.info("[%s] %d événements récupérés depuis Teams", display, len(events))

        # IDs Teams présents dans la fenêtre pour cet utilisateur
        teams_ids_fenetre = {e['id'] for e in events if not e.get('isCancelled')}

        # Événements Odoo de cet utilisateur dans la fenêtre avec un is_teams_event_id
        odoo_events_user = models.execute_kw(
            db_name, uid, config.ODOO_PASSWORD,
            'calendar.event', 'search_read',
            [[
                ('is_teams_event_id', '!=', False),
                ('user_id', '=', odoo_uid),
                ('start', '>=', d1),
                ('start', '<=', d2),
            ]],
            {'fields': ['id', 'is_teams_event_id', 'name', 'start'], 'limit': 10000}
        )

        user_created = 0
        user_updated = 0
        user_skipped = 0
        user_deleted = 0

        for event in events:
            if event.get('isCancelled'):
                continue

            event_id = event['id']
            new_vals = build_event_vals(event, odoo_uid, partner_id)

            if event_id in existing_events:
                # ── Mise à jour si nécessaire ─────────────────────────────
                existing = existing_events[event_id]
                diff = compute_update_vals(new_vals, existing)
                if diff:
                    try:
                        models.execute_kw(
                            db_name, uid, config.ODOO_PASSWORD,
                            'calendar.event', 'write',
                            [[existing['odoo_id']], diff]
                        )
                        user_updated += 1
                        total_updated += 1
                        _logger.info(
                            "[%s] ✏️  Mis à jour id=%s : %s %s | champs: %s",
                            display, existing['odoo_id'],
                            event['start']['dateTime'][:16], event.get('subject', ''),
                            ', '.join(diff.keys())
                        )
                    except Exception:
                        _logger.exception(
                            "[%s] ❌ Erreur mise à jour : %s %s",
                            display, event['start']['dateTime'][:16], event.get('subject', '')
                        )
                else:
                    user_skipped += 1
                    total_skipped += 1
            else:
                # ── Création ─────────────────────────────────────────────
                try:
                    new_id = models.execute_kw(
                        db_name, uid, config.ODOO_PASSWORD,
                        'calendar.event', 'create', [new_vals]
                    )
                    existing_events[event_id] = {
                        'odoo_id'           : new_id,
                        'name'              : new_vals['name'],
                        'start'             : new_vals['start'],
                        'stop'              : new_vals['stop'],
                        'description'       : new_vals['description'],
                        'videocall_location': new_vals['videocall_location'],
                    }
                    user_created += 1
                    total_created += 1
                    _logger.info(
                        "[%s] ✅ Créé id=%s : %s %s",
                        display, new_id, event['start']['dateTime'][:16], event.get('subject', '')
                    )
                except Exception:
                    _logger.exception(
                        "[%s] ❌ Erreur création : %s %s",
                        display, event['start']['dateTime'][:16], event.get('subject', '')
                    )

        # ── Suppressions : événements Odoo absents de Teams dans la fenêtre ──
        for odoo_ev in odoo_events_user:
            if odoo_ev['is_teams_event_id'] not in teams_ids_fenetre:
                try:
                    models.execute_kw(
                        db_name, uid, config.ODOO_PASSWORD,
                        'calendar.event', 'unlink',
                        [[odoo_ev['id']]]
                    )
                    existing_events.pop(odoo_ev['is_teams_event_id'], None)
                    user_deleted += 1
                    total_deleted += 1
                    _logger.info(
                        "[%s] 🗑️  Supprimé id=%s : %s %s",
                        display, odoo_ev['id'],
                        str(odoo_ev['start'])[:16], odoo_ev['name']
                    )
                except Exception:
                    _logger.exception(
                        "[%s] ❌ Erreur suppression id=%s : %s",
                        display, odoo_ev['id'], odoo_ev['name']
                    )

        _logger.info("[%s] Terminé. Créés: %d | Mis à jour: %d | Inchangés: %d | Supprimés: %d", display, user_created, user_updated, user_skipped, user_deleted)

    _logger.info(
        "Terminé global. Créés: %d | Mis à jour: %d | Inchangés: %d | Supprimés: %d",
        total_created, total_updated, total_skipped, total_deleted
    )


if __name__ == '__main__':
    main()
