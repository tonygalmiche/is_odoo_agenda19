# -*- coding: utf-8 -*-
import base64
from uuid import uuid4

from odoo import api, fields, models, _
from odoo.addons.google_calendar.models.google_sync import google_calendar_token
from odoo.addons.google_calendar.utils.google_calendar import GoogleCalendarService
import logging
_logger = logging.getLogger(__name__)


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    @api.model
    def search_read(self, *args, **kwargs):
        # HACK pour ne lister que les événements acceptés ou incertains
        #      ne pas lister les événements déclinés
        if 'domain' in kwargs:
            for idx, elt in enumerate(kwargs['domain']):
                if elt and elt[0] == 'partner_ids':
                    kwargs['domain'][idx][0] = 'is_invitation_acceptee_ids'
        return super(CalendarEvent, self).search_read(*args, **kwargs)

    def synchroniser_google_user(self,event,user):
        if user:
            with google_calendar_token(user.sudo()) as token:
                if token:
                    _logger.warning("## token = %s" % (token))
                    headers = {'Content-type': 'application/json', 'Authorization': 'Bearer %s' % token}
                    params = {'access_token': token}
                    values = event._google_values()
                    _logger.warning("## values = %s" % (values))
                    google_service = GoogleCalendarService(self.with_user(user).env['google.service'])
                    event_id = values.get('id')
                    _logger.warning("## synchroniser_google_action event_id = %s" % (event_id))
                    try:
                        _logger.warning("## _google_patch event = %s" % (event))
                        event.with_user(user)._google_patch(google_service, event_id, values, timeout=3)
                    except:
                        _logger.exception("## _google_patch  ERROR event = %s" % (event))


    def synchroniser_google_action(self):
        for obj in self.browse(self.env.context['active_ids']):
            for line in obj.attendee_ids:
                user=line.is_user_id
                _logger.warning("## synchroniser_google_action user = %s" % (user.login))
                if user:
                    self.synchroniser_google_user(obj,user)


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
        if 'start' in values:
            start_date = fields.Datetime.to_datetime(values.get('start'))
            # Only notify on future events
            if start_date and start_date >= fields.Datetime.now():
                for attendee in self.attendee_ids:
                    if attendee.partner_id.id != self.partner_id.id and attendee.state in ['accepted', 'declined']:
                        attendee.state = 'needsAction'
        return super(CalendarEvent, self).write(values)

    is_invitation_refusee_ids  = fields.Many2many(comodel_name='res.partner', relation='calendar_event_res_partner_refusee', column1="event_id", column2="partner_id", string="Utilisateurs ayant refusés")
    is_invitation_acceptee_ids = fields.Many2many(comodel_name='res.partner', relation='calendar_event_res_partner_acceptee', column1="event_id", column2="partner_id", string="Utilisateurs ayant acceptés")

    is_teams_event_id = fields.Char('Teams event_id', copy=False, index=True)
    is_teams_ical_uid = fields.Char('Teams iCalUId' , copy=False, index=True)


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

    def _accepter_invitation_actions(self):
        for obj in self.browse(self.env.context['active_ids']):
            obj.state="accepted"

    def _refuser_invitation_actions(self):
        for obj in self.browse(self.env.context['active_ids']):
            obj.state="declined"

    def write(self, vals):
        res = super(CalendarAttendee, self).write(vals)
        self.env['calendar.event'].synchroniser_google_user(self.event_id,self.is_user_id)
        self.synchro_refusee_acceptee()
        if 'state' in vals and vals['state'] == 'declined':
            self.send_mail_decline()
        return res

    def send_mail_decline(self):
        attendee = self
        ics_files = self.mapped('event_id')._get_ics_file()
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

    @api.model
    def create(self, vals):
        res = super(CalendarAttendee, self).create(vals)
        res.synchro_refusee_acceptee()
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
