# -*- coding: utf-8 -*-
from odoo import models, fields
from odoo.addons.is_odoo_agenda19.models.calendar import GOOGLE_SYNC_HORIZON_DAYS
from dateutil.relativedelta import relativedelta
import logging
_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    def action_supprimer_google_events(self):
        """Supprime dans Google tous les événements connus (is_google_event_id)
        pour cet utilisateur dans la fenêtre de GOOGLE_SYNC_HORIZON_DAYS jours,
        et remet is_google_event_id à False."""
        self.ensure_one()
        from odoo.addons.google_calendar.models.google_sync import google_calendar_token
        from odoo.addons.google_calendar.utils.google_calendar import GoogleCalendarService
        from odoo.addons.is_odoo_agenda19.models.calendar import _google_call_with_retry

        now = fields.Datetime.now()
        limit = now + relativedelta(days=GOOGLE_SYNC_HORIZON_DAYS)
        attendees = self.env['calendar.attendee'].search([
            ('is_user_id', '=', self.id),
            ('is_google_event_id', '!=', False),
            ('event_id.start', '>=', now),
            ('event_id.start', '<=', limit),
        ])
        user_sudo = self.sudo()
        if not user_sudo.google_calendar_rtoken or user_sudo.google_synchronization_stopped:
            return
        total = len(attendees)
        _logger.info("## suppr: %s événements à supprimer pour user=%s", total, self.login)
        with google_calendar_token(user_sudo) as token:
            if not token:
                return
            gs = GoogleCalendarService(
                self.env['google.service'].with_user(self).with_context(send_updates=False)
            )
            for idx, attendee in enumerate(attendees, start=1):
                try:
                    _logger.info("## suppr: %s/%s user=%s id=%s start=%s",
                                 idx, total, self.login, attendee.is_google_event_id, attendee.event_id.start)
                    _google_call_with_retry(gs.delete, attendee.is_google_event_id, token=token, timeout=3)
                except Exception:
                    _logger.exception("## suppr: ERROR user=%s id=%s", self.login, attendee.is_google_event_id)
        attendees.with_context(skip_google_sync=True).write({'is_google_event_id': False})
        _logger.info("## suppr: terminé, %s attendees réinitialisés", total)

    def action_synchroniser_google(self):
        """Lance une synchronisation Odoo → Google pour cet utilisateur
        (fenêtre GOOGLE_SYNC_HORIZON_DAYS jours), sans suppression préalable."""
        self.ensure_one()
        now = fields.Datetime.now()
        limit = now + relativedelta(days=GOOGLE_SYNC_HORIZON_DAYS)
        attendees = self.env['calendar.attendee'].search([
            ('is_user_id', '=', self.id),
            ('event_id.start', '>=', now),
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

    def action_reinitialiser_google_sync(self):
        """Supprime les événements Google connus puis les recrée proprement
        pour cet utilisateur (fenêtre GOOGLE_SYNC_HORIZON_DAYS jours)."""
        self.ensure_one()
        self.env['calendar.attendee'].sudo().reinitialiser_google_sync_action(user_id=self.id)
