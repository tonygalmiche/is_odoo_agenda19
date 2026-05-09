# -*- coding: utf-8 -*-
from odoo import models


class ResUsers(models.Model):
    _inherit = 'res.users'

    def action_reinitialiser_google_sync(self):
        """Supprime tous les événements Google connus pour cet utilisateur,
        réinitialise les is_google_event_id, puis relance une synchro propre
        pour les événements futurs (≤ 12 mois)."""
        self.ensure_one()
        attendees = self.env['calendar.attendee'].search([
            ('is_user_id', '=', self.id),
            ('is_google_event_id', '!=', False),
        ])
        self.env['calendar.attendee'].sudo().browse(attendees.ids).reinitialiser_google_sync_action()
