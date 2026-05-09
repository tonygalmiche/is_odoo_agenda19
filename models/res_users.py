# -*- coding: utf-8 -*-
from odoo import models, fields
from odoo.addons.is_odoo_agenda19.models.calendar import GOOGLE_SYNC_HORIZON_DAYS, GOOGLE_SYNC_HORIZON_PAST_DAYS
from dateutil.relativedelta import relativedelta
import logging
_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    def action_supprimer_google_events(self):
        """Supprime dans Google les événements de la plage dont le titre correspond
        à un événement Odoo (orphelins inclus). Les événements créés manuellement
        dans Google et absents d'Odoo sont conservés."""
        self.ensure_one()
        from odoo.addons.google_calendar.models.google_sync import google_calendar_token
        from odoo.addons.google_calendar.utils.google_calendar import GoogleCalendarService
        from odoo.addons.is_odoo_agenda19.models.calendar import _google_call_with_retry

        now = fields.Datetime.now()
        past = now - relativedelta(days=GOOGLE_SYNC_HORIZON_PAST_DAYS)
        limit = now + relativedelta(days=GOOGLE_SYNC_HORIZON_DAYS)
        user_sudo = self.sudo()
        if not user_sudo.google_calendar_rtoken or user_sudo.google_synchronization_stopped:
            return

        # Noms des événements Odoo dans la plage pour cet utilisateur
        attendees_odoo = self.env['calendar.attendee'].search([
            ('is_user_id', '=', self.id),
            ('event_id.start', '>=', past),
            ('event_id.start', '<=', limit),
        ])
        odoo_names = {a.event_id.name for a in attendees_odoo if a.event_id.name}
        _logger.warning("## suppr: %s noms d'événements Odoo dans la plage pour user=%s : %s",
                        len(odoo_names), self.login, odoo_names)

        with google_calendar_token(user_sudo) as token:
            if not token:
                return
            gs = GoogleCalendarService(
                self.env['google.service'].with_user(self).with_context(send_updates=False)
            )
            url = "/calendar/v3/calendars/primary/events"
            headers = {'Content-type': 'application/json'}
            params = {
                'access_token': token,
                'timeMin': past.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'timeMax': limit.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'singleEvents': 'true',
                'maxResults': 2500,
            }
            try:
                _status, data, _t = self.env['google.service'].with_user(self)._do_request(
                    url, params, headers, method='GET', timeout=30
                )
            except Exception:
                _logger.exception("## suppr: erreur lors du listing Google pour user=%s", self.login)
                return

            google_events = data.get('items', [])
            _logger.warning("## suppr: %s événements trouvés dans Google pour user=%s", len(google_events), self.login)
            to_delete = [(ev.get('id'), ev.get('summary', ''), ev.get('start', {}).get('dateTime') or ev.get('start', {}).get('date', '?'))
                         for ev in google_events if ev.get('summary', '') in odoo_names and ev.get('id')]
            to_skip = [ev for ev in google_events if ev.get('summary', '') not in odoo_names]
            _logger.warning("## suppr: %s à supprimer, %s à conserver (créés manuellement)", len(to_delete), len(to_skip))
            total = len(to_delete)
            for idx, (gid, gname, gstart) in enumerate(to_delete, start=1):
                _logger.warning("## suppr: %s/%s DELETE id=%s | name=%s | start=%s", idx, total, gid, gname, gstart)
                try:
                    _google_call_with_retry(gs.delete, gid, token=token, timeout=3)
                except Exception:
                    _logger.exception("## suppr: ERROR delete Google id=%s", gid)

        # Réinitialiser les is_google_event_id en base (plage passé+futur)
        attendees_odoo.with_context(skip_google_sync=True).write({'is_google_event_id': False})
        _logger.warning("## suppr: terminé, %s attendees réinitialisés", len(attendees_odoo))

    def action_synchroniser_google(self):
        """Lance une synchronisation Odoo → Google pour cet utilisateur
        (fenêtre GOOGLE_SYNC_HORIZON_DAYS jours), sans suppression préalable."""
        self.ensure_one()
        now = fields.Datetime.now()
        past = now - relativedelta(days=GOOGLE_SYNC_HORIZON_PAST_DAYS)
        limit = now + relativedelta(days=GOOGLE_SYNC_HORIZON_DAYS)
        attendees = self.env['calendar.attendee'].search([
            ('is_user_id', '=', self.id),
            ('event_id.start', '>=', past),
            ('event_id.start', '<=', limit),
        ])
        total = len(attendees)
        _logger.info("## sync: %s événements à synchroniser pour user=%s", total, self.login)
        CalendarEvent = self.env['calendar.event']
        for idx, attendee in enumerate(attendees, start=1):
            try:
                CalendarEvent.synchroniser_google_user(attendee.event_id, self, attendee=attendee, idx=idx, total=total)
            except Exception:
                _logger.exception("## sync: ERROR user=%s event=%s", self.login, attendee.event_id.id)
        _logger.info("## sync: terminé pour user=%s", self.login)

    def action_supprimer_doublons_odoo(self):
        """Supprime les événements Odoo en doublon dans la plage J-7/J+15 :
        pour chaque combinaison (name, start, stop), ne conserve qu'un seul
        enregistrement (celui avec google_id en priorité, sinon le plus ancien).
        Les doublons sont archivés (active=False)."""
        self.ensure_one()
        now = fields.Datetime.now()
        past = now - relativedelta(days=GOOGLE_SYNC_HORIZON_PAST_DAYS)
        limit = now + relativedelta(days=GOOGLE_SYNC_HORIZON_DAYS)

        # Chercher tous les events de la plage dont cet user est participant
        attendees = self.env['calendar.attendee'].search([
            ('is_user_id', '=', self.id),
            ('event_id.start', '>=', past),
            ('event_id.start', '<=', limit),
        ])
        event_ids = attendees.mapped('event_id.id')
        events = self.env['calendar.event'].browse(event_ids)

        # Regrouper par (name, start, stop)
        from collections import defaultdict
        groups = defaultdict(list)
        for ev in events:
            key = (ev.name or '', str(ev.start), str(ev.stop))
            groups[key].append(ev)

        to_archive = self.env['calendar.event']
        for key, evs in groups.items():
            if len(evs) <= 1:
                continue
            # Garder celui avec google_id en priorité, sinon le plus ancien (id le plus petit)
            with_gid = [e for e in evs if e.google_id]
            keeper = with_gid[0] if with_gid else min(evs, key=lambda e: e.id)
            duplicates = [e for e in evs if e.id != keeper.id]
            _logger.warning("## doublons: key=%s | conservé id=%s | supprimés ids=%s",
                            key, keeper.id, [e.id for e in duplicates])
            to_archive |= self.env['calendar.event'].browse([e.id for e in duplicates])

        if to_archive:
            _logger.warning("## doublons: archivage de %s événements en doublon", len(to_archive))
            to_archive.with_context(dont_notify=True).write({'active': False})
        else:
            _logger.warning("## doublons: aucun doublon trouvé pour user=%s", self.login)
        _logger.warning("## doublons: terminé pour user=%s", self.login)

    def action_reinitialiser_google_sync(self):
        """Supprime les événements Google connus puis les recrée proprement
        pour cet utilisateur (fenêtre GOOGLE_SYNC_HORIZON_DAYS jours)."""
        self.ensure_one()
        self.env['calendar.attendee'].sudo().reinitialiser_google_sync_action(user_id=self.id)
