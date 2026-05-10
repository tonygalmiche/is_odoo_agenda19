# -*- coding: utf-8 -*-
import base64
from uuid import uuid4

import time
from dateutil.relativedelta import relativedelta
from requests.exceptions import HTTPError

from odoo import api, fields, models, _
from odoo.tools import SQL
from odoo.addons.google_calendar.models.google_sync import google_calendar_token
from odoo.addons.google_calendar.utils.google_calendar import GoogleCalendarService
import logging
_logger = logging.getLogger(__name__)

# Fenêtre de synchronisation Google Agenda
GOOGLE_SYNC_HORIZON_PAST_DAYS = 14   # jours dans le passé
GOOGLE_SYNC_HORIZON_DAYS = 365       # jours dans le futur


def _google_call_with_retry(func, *args, max_retries=3, **kwargs):
    """Appelle func(*args, **kwargs) en réessayant sur rateLimitExceeded (403)."""
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 410:
                # Ressource déjà supprimée côté Google → ignorer silencieusement
                return None
            if e.response is not None and e.response.status_code == 403 and attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s
                _logger.warning("## Google 403, retry dans %ss (tentative %s/%s)", wait, attempt + 1, max_retries)
                time.sleep(wait)
            else:
                raise


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    @api.model_create_multi
    def create(self, vals_list):
        # Désactiver le sync natif Google (google_sync.create appelle _google_insert
        # directement, indépendamment de _sync_odoo2google). Notre module gère la
        # synchronisation via synchroniser_google_user / CalendarAttendee.write.
        for vals in vals_list:
            vals['need_sync'] = False
        return super().create(vals_list)

    @api.model
    def search_read(self, *args, **kwargs):
        # HACK pour ne lister que les événements acceptés ou incertains
        #      ne pas lister les événements déclinés
        if 'domain' in kwargs:
            for idx, elt in enumerate(kwargs['domain']):
                if elt and elt[0] == 'partner_ids':
                    kwargs['domain'][idx][0] = 'is_invitation_acceptee_ids'
        return super(CalendarEvent, self).search_read(*args, **kwargs)

    def synchroniser_google_user(self, event, user, attendee=None, idx=None, total=None):
        """Synchronise un événement vers le Google Agenda d'un utilisateur.

        Chaque utilisateur reçoit son propre événement indépendant dans son agenda
        Google (sans liste d'attendees, pour éviter les invitations multiples et les
        erreurs de quota). La liste des participants est ajoutée dans la Description.
        Sens unique : Odoo → Google uniquement.
        """
        if not user:
            return
        # Ne pas synchroniser les événements archivés
        if not event.active:
            return
        # Ne synchroniser que les événements futurs ou en cours, et dans les 12 prochains mois
        now = fields.Datetime.now()
        past = now - relativedelta(days=GOOGLE_SYNC_HORIZON_PAST_DAYS)
        if event.start and (event.start < past or event.start > now + relativedelta(days=GOOGLE_SYNC_HORIZON_DAYS)):
            return
        user_sudo = user.sudo()
        if not user_sudo.google_calendar_rtoken or user_sudo.google_synchronization_stopped:
            return
        progress = ("%s/%s " % (idx, total)) if idx is not None and total is not None else ""
        with google_calendar_token(user_sudo) as token:
            if not token:
                return

            gs = GoogleCalendarService(
                self.with_user(user).with_context(send_updates=False).env['google.service']
            )

            event_start = event.start

            # Participant refusé → supprimer l'événement de son agenda Google
            if attendee and attendee.state == 'declined':
                if attendee.is_google_event_id:
                    try:
                        _logger.info("## %sGoogle delete user=%s event=%s start=%s name=%s", progress, user.login, attendee.is_google_event_id, event_start, event.name)
                        _google_call_with_retry(gs.delete, attendee.is_google_event_id, token=token, timeout=3)
                        attendee.sudo().with_context(skip_google_sync=True).write({'is_google_event_id': False})
                    except Exception:
                        _logger.exception("## %sGoogle delete ERROR user=%s start=%s", progress, user.login, event_start)
                return

            # Construire les valeurs de base depuis Odoo
            values = event._google_values()

            # Supprimer les attendees pour ne pas envoyer d'invitations Google
            # (évite les erreurs quotaExceeded) et l'id du créateur.
            # Supprimer aussi les reminders : les rappels sont gérés par Odoo,
            # pas par Google (évite l'erreur eventRemindersCountExceedsLimit).
            values.pop('attendees', None)
            values.pop('id', None)
            values.pop('reminders', None)

            # Ajouter la liste des participants dans la Description
            participant_names = [att.partner_id.name for att in event.attendee_ids if att.partner_id]
            if participant_names:
                participants_block = "Participants :\n" + "\n".join("- " + n for n in participant_names)
                existing_desc = values.get('description') or ''
                # Remplacer un bloc Participants existant ou ajouter à la fin
                if 'Participants :' in existing_desc:
                    before = existing_desc[:existing_desc.index('Participants :')].rstrip()
                    values['description'] = (before + "\n\n" + participants_block).strip() if before else participants_block
                else:
                    values['description'] = (existing_desc + "\n\n" + participants_block).strip() if existing_desc else participants_block

            attendee_google_id = attendee.is_google_event_id if attendee else False
            if attendee_google_id:
                # L'événement existe déjà dans son agenda → patch
                try:
                    _logger.info("## %sGoogle patch user=%s event=%s start=%s name=%s", progress, user.login, attendee_google_id, event_start, event.name)
                    _google_call_with_retry(gs.patch, attendee_google_id, values, token=token, timeout=3)
                except Exception:
                    _logger.exception("## %sGoogle patch ERROR user=%s start=%s", progress, user.login, event_start)
            else:
                # Première synchro → insertion dans son agenda
                try:
                    _logger.info("## %sGoogle insert user=%s start=%s name=%s", progress, user.login, event_start, event.name)
                    google_values = _google_call_with_retry(gs.insert, values, token=token, timeout=3, need_video_call=False)
                    new_google_id = google_values and google_values.get('id')
                    if new_google_id and attendee:
                        attendee.sudo().with_context(skip_google_sync=True).write(
                            {'is_google_event_id': new_google_id}
                        )
                        # Empêcher le module natif de re-synchroniser cet événement
                        event.with_context(dont_notify=True).write({'need_sync': False})
                except Exception:
                    _logger.exception("## %sGoogle insert ERROR user=%s start=%s", progress, user.login, event_start)


    def _skip_send_mail_status_update(self):
        """Annule le blocage des rappels email introduit par google_calendar.

        Le natif retourne True si l'utilisateur a Google Calendar synchronisé,
        empêchant l'envoi des mails de rappel Odoo (Google était censé les envoyer).
        Comme nous gérons la synchro nous-mêmes, Odoo doit toujours envoyer les rappels.
        """
        return False

    def action_unlink_event(self, attendee_id=None, recurrence=False):
        """Surcharge pour corriger le bug natif Odoo/Google : quand
        _has_any_active_synchronization() est True et plusieurs participants,
        le natif appelle self.unlink() sur UN SEUL event en ignorant recurrence='all'.
        On rétablit le comportement correct selon le choix de l'utilisateur.
        """
        switch = {'next': 'future_events', 'all': 'all_events'}
        if recurrence in switch:
            self.action_mass_deletion(switch[recurrence])
            return {'type': 'ir.actions.act_url', 'target': 'self', 'url': '/odoo/calendar'}
        # Pour 'one' ou False : comportement natif
        return super().action_unlink_event(attendee_id=attendee_id, recurrence=recurrence)

    def action_mass_deletion(self, recurrence_update_setting):
        return super().action_mass_deletion(recurrence_update_setting)

    def synchroniser_google_action(self):
        for obj in self.browse(self.env.context['active_ids']):
            attendees_with_user = [(line, line.is_user_id) for line in obj.attendee_ids if line.is_user_id]
            total = len(attendees_with_user)
            for idx, (line, user) in enumerate(attendees_with_user, start=1):
                _logger.info("## synchroniser_google_action user=%s", user.login)
                self.synchroniser_google_user(obj, user, line, idx=idx, total=total)


    @api.onchange('partner_ids','start','duration')
    def _compute_is_alerte(self):

        for obj in self:
            alertes=[]
            for partner in obj.partner_ids:
                SQL="""
                    SELECT rp.name, e.name event_name, e.privacy
                    FROM calendar_event e join calendar_attendee a on e.id=a.event_id
                                          join res_partner      rp on a.partner_id=rp.id 
                    WHERE 
                        e.active='t' and 
                        e.id<>%s and
                        a.state not in ('declined') and
                        a.partner_id=%s and ( 
                            (e.start<%s and e.stop>%s) or
                            (e.start<%s and e.stop>%s) or
                            (e.start<=%s and e.stop>=%s) or
                            (e.start>=%s and e.stop<=%s) or
                            (e.start=%s and e.stop=%s)
                        )
                    ORDER BY rp.name, e.name
                """
                self.env.cr.execute(SQL, [
                    obj._origin.id or 0, partner._origin.id, 
                    obj.start, obj.start, 
                    obj.stop , obj.stop, 
                    obj.start, obj.stop, 
                    obj.start, obj.stop, 
                    obj.start, obj.stop
                ])
                events=self.env.cr.fetchall()
                for e in events:
                    if e[2] == 'private':
                        event_name = 'Occupé(e)'
                    else:
                        event_name = e[1]
                    msg=e[0]+" : "+event_name
                    alertes.append(msg)
            if len(alertes):
                alertes="\n".join(alertes)
            else:
                alertes=False
            obj.is_alerte=alertes


    @api.onchange('partner_ids')
    def _compute_is_participants(self):
        for obj in self:
            lines=[]
            for attendee in obj.attendee_ids:
                if attendee.state == "declined":
                    lines.append('-  <del style="color:red">'+attendee.partner_id.name+"</del>")
                if attendee.state == "accepted":
                    lines.append('-  <b style="color:green">'+attendee.partner_id.name+"</b>")
                if attendee.state not in ["declined","accepted"]:
                    lines.append('-  <i style="color:gray">'+attendee.partner_id.name+"</i>")
            obj.is_participants = "<br />".join(lines)


    is_alerte       = fields.Text('Alerte'      , copy=False, compute=_compute_is_alerte)
    is_participants = fields.Html('Participants', copy=False, compute=_compute_is_participants, sanitize=False)

    @api.depends('user_id.partner_id.is_calendar_color')
    def _compute_is_creator_calendar_color(self):
        for event in self:
            partner = event.user_id.partner_id
            color = partner.is_calendar_color if partner else 0
            # Si color=0, utiliser l'id du partenaire comme index unique
            event.is_creator_calendar_color = color if color else (partner.id if partner else 0)

    is_creator_calendar_color = fields.Integer(
        'Couleur créateur', compute=_compute_is_creator_calendar_color)


    def unlink(self):
        # Avant la suppression définitive, supprimer l'événement du Google Agenda
        # de chaque participant non-créateur ayant un is_google_event_id.
        # On collecte les infos nécessaires AVANT le super() car après,
        # les enregistrements n'existent plus.
        for event in self:
            for attendee in event.attendee_ids:
                user = attendee.is_user_id
                if not user:
                    continue
                if not attendee.is_google_event_id:
                    continue
                user_sudo = user.sudo()
                if not user_sudo.google_calendar_rtoken or user_sudo.google_synchronization_stopped:
                    continue
                with google_calendar_token(user_sudo) as token:
                    if token:
                        try:
                            gs = GoogleCalendarService(self.with_user(user).env['google.service'])
                            _logger.info("## Google delete (unlink) user=%s event=%s", user.login, attendee.is_google_event_id)
                            gs.delete(attendee.is_google_event_id, token=token, timeout=3)
                        except Exception:
                            _logger.exception("## Google delete (unlink) ERROR user=%s", user.login)
        return super(CalendarEvent, self).unlink()

    def _ajouter_invitation_responsable_action(self):
        for obj in self.browse(self.env.context['active_ids']):
            for partner in obj.partner_ids:
                if partner not in obj.attendee_ids.partner_id:
                    vals={
                        "event_id"  : obj.id,
                        "partner_id": partner.id,
                        "state"     : "accepted",
                    }
                    self.env['calendar.attendee'].create(vals)

    def _mise_a_jour_acceptee_refusee_action(self):
        for obj in self.browse(self.env.context['active_ids']):
            for attendee in obj.attendee_ids:
                attendee.synchro_refusee_acceptee()


    # def agenda_journee_action(self):
    #     for obj in self:
    #         res= {
    #             'name': 'Agenda',
    #             'view_mode': 'calendar',
    #             'res_model': 'calendar.event',
    #             'res_id': obj.id,
    #             'type': 'ir.actions.act_window',
    #             'view_id': self.env.ref('pg_odoo_agenda.view_calendar_event_calendar_journee').id,
    #             'domain': [["start","<=","2021-07-04 21:59:59"],["stop",">=","2021-06-27 22:00:00"],["partner_ids","in",[3,7]]],
    #         }
    #         return res

    def write(self, values):
        # Quand la date/heure change, on remet les attendees qui avaient déjà
        # répondu (accepté ou refusé) en état "needsAction" pour qu'ils
        # confirment de nouveau. On utilise skip_google_sync=True pour éviter
        # de déclencher une synchro Google sur ce seul changement d'état.
        if 'start' in values:
            start_date = fields.Datetime.to_datetime(values.get('start'))
            # Only notify on future events
            if start_date and start_date >= fields.Datetime.now():
                for attendee in self.attendee_ids:
                    if attendee.partner_id.id != self.partner_id.id and attendee.state in ['accepted', 'declined']:
                        attendee.with_context(skip_google_sync=True).write({'state': 'needsAction'})
        # Si l'événement est archivé (supprimé côté Odoo), supprimer l'entrée
        # du Google Agenda de chaque participant non-créateur ayant un
        # is_google_event_id. On le fait AVANT le super() car après, les
        # tokens ne sont plus accessibles proprement.
        if values.get('active') is False and not self.env.context.get('skip_google_sync'):
            for event in self:
                for attendee in event.attendee_ids:
                    user = attendee.is_user_id
                    if not user:
                        continue
                    if not attendee.is_google_event_id:
                        continue
                    user_sudo = user.sudo()
                    if not user_sudo.google_calendar_rtoken or user_sudo.google_synchronization_stopped:
                        # Vider quand même pour ne pas avoir un ID orphelin
                        attendee.sudo().with_context(skip_google_sync=True).write({'is_google_event_id': False})
                        continue
                    with google_calendar_token(user_sudo) as token:
                        if token:
                            try:
                                gs = GoogleCalendarService(
                                    self.with_user(user).env['google.service']
                                )
                                _logger.info("## Google delete (archive) participant user=%s event=%s", user.login, attendee.is_google_event_id)
                                gs.delete(attendee.is_google_event_id, token=token, timeout=3)
                            except Exception:
                                _logger.exception("## Google delete (archive) ERROR user=%s", user.login)
                            finally:
                                # Toujours vider l'ID pour que le prochain write(active=True)
                                # déclenche un insert et non un patch sur un ID supprimé
                                attendee.sudo().with_context(skip_google_sync=True).write({'is_google_event_id': False})
        res = super(CalendarEvent, self).write(values)
        # Quand un événement est réactivé (active=True, ex. base event après modification
        # d'une série), il faut le re-synchroniser vers Google car is_google_event_id
        # a été vidé lors de l'archivage précédent.
        if values.get('active') is True and not self.env.context.get('dont_notify') and not self.env.context.get('skip_google_sync'):
            for event in self:
                attendees = event.attendee_ids
                total = len(attendees)
                for idx, attendee in enumerate(attendees, start=1):
                    self.env['calendar.event'].sudo().synchroniser_google_user(
                        event, attendee.is_user_id, attendee, idx=idx, total=total
                    )
        # Après la sauvegarde, synchroniser vers Google Agenda pour chaque
        # participant ayant activé la synchro Google, mais uniquement si l'un
        # des champs métier pertinents a été modifié. On itère sur self car
        # write() peut porter sur plusieurs événements à la fois.
        # dont_notify=True signifie que c'est le cron Google → Odoo qui écrit :
        # on ne relance surtout pas une synchro Odoo → Google dans ce cas.
        sync_fields = {'name', 'start', 'stop', 'duration', 'description', 'location', 'allday', 'partner_ids', 'attendee_ids'}
        if sync_fields & set(values.keys()) and not self.env.context.get('dont_notify'):
            for event in self:
                attendees = event.attendee_ids
                total = len(attendees)
                for idx, attendee in enumerate(attendees, start=1):
                    if event.follow_recurrence and not attendee.is_google_event_id:
                        continue
                    self.env['calendar.event'].sudo().synchroniser_google_user(
                        event, attendee.is_user_id, attendee, idx=idx, total=total
                    )
        return res

    def _sync_google2odoo(self, google_events, write_dates=None, default_reminders=()):
        """Bloque la synchronisation Google → Odoo (sens unique Odoo → Google uniquement)."""
        _logger.info("## _sync_google2odoo bloqué par is_odoo_agenda19 (%s events ignorés)", len(list(google_events)))
        return self.browse()

    def _sync_odoo2google(self, google_service):
        """Bloque la synchronisation native Odoo → Google (gérée par synchroniser_google_user)."""
        _logger.info("## _sync_odoo2google bloqué par is_odoo_agenda19 (%s events ignorés)", len(self))
        return

    is_invitation_refusee_ids  = fields.Many2many(comodel_name='res.partner', relation='calendar_event_res_partner_refusee', column1="event_id", column2="partner_id", string="Utilisateurs ayant refusés")
    is_invitation_acceptee_ids = fields.Many2many(comodel_name='res.partner', relation='calendar_event_res_partner_acceptee', column1="event_id", column2="partner_id", string="Utilisateurs ayant acceptés")

    is_teams_event_id = fields.Char('Teams event_id', copy=False, index=True)
    is_teams_ical_uid = fields.Char('Teams iCalUId' , copy=False, index=True)


class CalendarRecurrence(models.Model):
    """Bloque le sync natif Google sur les récurrences.

    Quand une récurrence est créée, google_sync.create() l'insère dans Google
    comme un événement récurrent natif (N occurrences visibles). Notre module
    insère ensuite N événements individuels via synchroniser_google_user.
    Résultat : 2N événements dans Google. Forcer need_sync=False bloque le natif.
    """
    _inherit = 'calendar.recurrence'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['need_sync'] = False
        return super().create(vals_list)

    def _sync_google2odoo(self, google_events, write_dates=None, default_reminders=()):
        _logger.info("## CalendarRecurrence._sync_google2odoo bloqué (%s events ignorés)", len(list(google_events)))
        return self.browse()

    def _sync_odoo2google(self, google_service):
        _logger.info("## CalendarRecurrence._sync_odoo2google bloqué (%s récurrences ignorées)", len(self))
        return


class CalendarAttendee(models.Model):
    _inherit = 'calendar.attendee'

    @api.depends('partner_id')
    def _compute_is_user_id(self):
        for obj in self:
            user_id=False
            if obj.partner_id:
                users = self.env['res.users'].search([
                    ('partner_id', '=', obj.partner_id.id),
                ])
                if users:
                    user_id=users[0].id
            obj.is_user_id=user_id
    is_user_id = fields.Many2one('res.users', 'Utilisateur', compute='_compute_is_user_id', store=True, readonly=True, index=True)
    is_google_event_id = fields.Char('Google Event ID (participant)', copy=False, index=True)

    def do_accept(self):
        for attendee in self:
            attendee.event_id.sudo().message_post(
                author_id=attendee.partner_id.id,
                body=_("%s has accepted the invitation", attendee.common_name),
                subtype_xmlid="calendar.subtype_invitation",
            )
        return self.sudo().write({'state': 'accepted'})

    def do_decline(self):
        for attendee in self:
            attendee.event_id.sudo().message_post(
                author_id=attendee.partner_id.id,
                body=_("%s has declined the invitation", attendee.common_name),
                subtype_xmlid="calendar.subtype_invitation",
            )
        return self.sudo().write({'state': 'declined'})

    def do_tentative(self):
        return self.sudo().write({'state': 'tentative'})

    def action_open_event(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'calendar.event',
            'res_id': self.event_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _accepter_invitation_actions(self):
        for obj in self.browse(self.env.context['active_ids']):
            obj.sudo().write({'state': 'accepted'})

    def _refuser_invitation_actions(self):
        for obj in self.browse(self.env.context['active_ids']):
            obj.sudo().write({'state': 'declined'})

    def write(self, vals):
        res = super(CalendarAttendee, self).write(vals)
        # Après chaque modification d'un attendee, on synchronise vers Google
        # Agenda — sauf si on est dans un contexte skip_google_sync=True (utilisé
        # pour éviter la récursion lors de l'écriture de is_google_event_id) ou
        # dont_notify=True (utilisé par le cron Google → Odoo, sens inverse).
        if not self.env.context.get('skip_google_sync') and not self.env.context.get('dont_notify'):
            total = len(self)
            for idx, attendee in enumerate(self, start=1):
                self.env['calendar.event'].sudo().synchroniser_google_user(
                    attendee.event_id, attendee.is_user_id, attendee, idx=idx, total=total
                )
            self.synchro_refusee_acceptee()
            if 'state' in vals and vals['state'] == 'declined':
                self.send_mail_decline()
        return res

    def send_mail_decline(self):
        for attendee in self:
            attendee._send_mail_decline_single()

    def _send_mail_decline_single(self):
        self.ensure_one()
        attendee = self
        ics_files = attendee.event_id._get_ics_file()
        template_xmlid = 'is_odoo_agenda19.calendar_template_meeting_change'
        invitation_template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if not invitation_template:
            _logger.warning("Template %s could not be found. %s not notified." % (template_xmlid, self))
            return
        calendar_view = self.env.ref('calendar.view_calendar_event_calendar')
        # prepare rendering context for mail template
        force_send = True
        ignore_recurrence = False
        colors = {
            'needsAction': 'grey',
            'accepted': 'green',
            'tentative': '#FFFF00',
            'declined': 'red'
        }
        rendering_context = dict(self.env.context)
        rendering_context.update({
            'colors': colors,
            'ignore_recurrence': ignore_recurrence,
            'action_id': self.env['ir.actions.act_window'].sudo().search([('view_id', '=', calendar_view.id)], limit=1).id,
            'dbname': self.env.cr.dbname,
            'base_url': self.env['ir.config_parameter'].sudo().get_param('web.base.url', default='http://localhost:8069'),
        })
        #
        event_id = attendee.event_id.id
        ics_file = ics_files.get(event_id)

        attachment_ids = []
        if ics_file:
            attachment = self.env['ir.attachment'].create({
                'name': 'invitation.ics',
                'mimetype': 'text/calendar',
                'datas': base64.b64encode(ics_file),
            })
            attachment_ids = [attachment.id]
        body = invitation_template.with_context(rendering_context)._render_field(
            'body_html',
            attendee.ids,
            compute_lang=True,
            options={'post_process': True})[attendee.id]

        subject = invitation_template._render_field(
            'subject',
            attendee.ids,
            compute_lang=True)[attendee.id]


        #** Permet d'envoyer un mail au responsable en changeant l'emetteur du mail
        email_from  = self.env.user.email_formatted
        author_id = self.env.user.id
        #if attendee.event_id.user_id.partner_id.id in attendee.partner_id.ids:
        #    email_from = "robot@plastigray.com"
        #    author_id  = False

        attendee.event_id.with_context(no_document=True).message_notify(
            email_from=email_from,
            author_id=author_id,
            body=body,
            subject="[odoo-agenda] "+subject,
            partner_ids=[attendee.event_id.user_id.partner_id.id],
            email_layout_xmlid='mail.mail_notification_light',
            attachment_ids=attachment_ids,
            force_send=force_send)

    @api.model_create_multi
    def create(self, vals_list):
        # On utilise @api.model_create_multi (Odoo 19) car Odoo peut créer
        # plusieurs calendar.attendee en une seule requête (ex. quand on
        # ajoute plusieurs participants d'un coup). On itère ensuite sur
        # chaque attendee individuellement pour éviter l'erreur ensure_one()
        # lors de l'accès à is_user_id.
        res = super(CalendarAttendee, self).create(vals_list)
        res.synchro_refusee_acceptee()
        total = len(res)
        for idx, attendee in enumerate(res, start=1):
            self.env['calendar.event'].sudo().synchroniser_google_user(
                attendee.event_id, attendee.is_user_id, attendee, idx=idx, total=total
            )
        return res

    def synchro_refusee_acceptee(self):
        for obj in self:
            declined = []
            accepted = []
            for attendee in obj.event_id.attendee_ids:
                if attendee.state == "declined":
                    declined.append(attendee.partner_id.id)
                else:
                    accepted.append(attendee.partner_id.id)
            obj.event_id.sudo().write({'is_invitation_refusee_ids': [(6, 0, declined)]})
            obj.event_id.sudo().write({'is_invitation_acceptee_ids': [(6, 0, accepted)]})

    def _log_attendees_dans_plage(self, now, limit, user_id=None):
        """Affiche dans les logs tous les attendees dans la fenêtre de synchro."""
        diag_domain = [
            ('event_id.start', '>=', now),
            ('event_id.start', '<=', limit),
        ]
        if user_id:
            diag_domain.append(('is_user_id', '=', user_id))
        all_attendees = self.search(diag_domain)
        _logger.warning("## reinit: %s attendees dans la plage (%s jours)", len(all_attendees), GOOGLE_SYNC_HORIZON_DAYS)
        for a in all_attendees:
            _logger.warning("## reinit: attendee id=%s | event_id=%s | name=%s | start=%s | google_id=%s | user=%s",
                            a.id, a.event_id.id, a.event_id.name, a.event_id.start,
                            a.is_google_event_id or '-', a.is_user_id.login if a.is_user_id else '-')

    def reinitialiser_google_sync_action(self, user_id=None):
        """Réinitialise la synchro Google pour les événements futurs (≤ 12 mois).

        Si user_id est fourni, traite uniquement cet utilisateur.
        1. Supprime dans Google tous les événements dont l'ID est connu,
           groupés par utilisateur (1 seule ouverture de token par user).
        2. Remet is_google_event_id à False.
        3. Recrée proprement un événement par attendee via INSERT.
        """
        active_ids = self.env.context.get('active_ids', [])
        now = fields.Datetime.now()
        past = now - relativedelta(days=GOOGLE_SYNC_HORIZON_PAST_DAYS)
        limit = now + relativedelta(days=GOOGLE_SYNC_HORIZON_DAYS)
        base_domain = [
            ('is_google_event_id', '!=', False),
            ('event_id.start', '>=', past),
            ('event_id.start', '<=', limit),
        ]
        if user_id:
            base_domain.append(('is_user_id', '=', user_id))

        if active_ids:
            attendees = self.browse(active_ids).filtered(
                lambda a: a.event_id.start and past <= a.event_id.start <= limit
                and (not user_id or a.is_user_id.id == user_id)
            )
        else:
            attendees = self.search(base_domain)

        # --- 0. Diagnostic ---
        self._log_attendees_dans_plage(past, limit, user_id=user_id)

        # --- 1. Supprimer par blocs d'utilisateur ---
        # Regrouper les google_event_id par utilisateur
        from collections import defaultdict
        by_user = defaultdict(list)  # {user_id: [(attendee, google_event_id), ...]}
        for attendee in attendees:
            user = attendee.is_user_id
            if user and attendee.is_google_event_id:
                by_user[user.id].append((attendee, attendee.is_google_event_id))

        total_users = len(by_user)
        for u_idx, (user_id, items) in enumerate(by_user.items(), start=1):
            user = self.env['res.users'].browse(user_id)
            user_sudo = user.sudo()
            if not user_sudo.google_calendar_rtoken or user_sudo.google_synchronization_stopped:
                continue
            with google_calendar_token(user_sudo) as token:
                if not token:
                    continue
                gs = GoogleCalendarService(
                    self.with_user(user).with_context(send_updates=False).env['google.service']
                )
                total_ev = len(items)
                for ev_idx, (attendee, google_event_id) in enumerate(items, start=1):
                    try:
                        _logger.info("## reinit: user %s/%s | event %s/%s | delete user=%s id=%s start=%s",
                                     u_idx, total_users, ev_idx, total_ev,
                                     user.login, google_event_id, attendee.event_id.start)
                        _google_call_with_retry(gs.delete, google_event_id, token=token, timeout=3)
                    except Exception:
                        _logger.exception("## reinit: delete ERROR user=%s id=%s", user.login, google_event_id)

        # --- 2. Réinitialiser les is_google_event_id ---
        attendees.with_context(skip_google_sync=True).write({'is_google_event_id': False})
        _logger.info("## reinit: %s attendees réinitialisés", len(attendees))

        # --- 3. Recréer proprement ---
        recreate_domain = [
            ('is_google_event_id', '=', False),
            ('event_id.start', '>=', past),
            ('event_id.start', '<=', limit),
        ]
        if user_id:
            recreate_domain.append(('is_user_id', '=', user_id))
        future_attendees = self.search(recreate_domain)
        total = len(future_attendees)
        _logger.info("## reinit: %s attendees à resynchroniser", total)
        for idx, attendee in enumerate(future_attendees, start=1):
            self.env['calendar.event'].sudo().synchroniser_google_user(
                attendee.event_id, attendee.is_user_id, attendee, idx=idx, total=total
            )


class CalendarAlarmManager(models.AbstractModel):
    _inherit = 'calendar.alarm_manager'

    @api.model
    def _get_notify_alert_extra_conditions(self):
        """Annule le filtre 'google_id IS NULL' ajouté par le module google_calendar.

        Le module natif bloque les rappels Odoo pour tous les événements ayant un
        google_id, car Google est censé envoyer les rappels à leur place.
        Ici, nous gérons la synchro nous-mêmes sans déléguer les rappels à Google,
        donc tous les événements doivent recevoir leurs rappels Odoo normalement.
        """
        return SQL("")
