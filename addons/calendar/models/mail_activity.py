# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, models, fields, tools, _


class MailActivityType(models.Model):
    _inherit = "mail.activity.type"

    category = fields.Selection(selection_add=[('meeting', 'Meeting')])


class MailActivity(models.Model):
    _inherit = "mail.activity"

    calendar_event_id = fields.Many2one('calendar.event', string="Calendar Meeting", ondelete='cascade')

    @api.multi
    def action_create_calendar_event(self):
        self.ensure_one()
        action = self.env.ref('calendar.action_calendar_event').read()[0]
        action['context'] = {
            'default_activity_type_id': self.activity_type_id.id,
            'default_res_id': self.env.context.get('default_res_id'),
            'default_res_model': self.env.context.get('default_res_model'),
            'default_name': self.summary or self.res_name,
            'default_description': self.note and tools.html2plaintext(self.note).strip() or '',
            'default_activity_ids': [(6, 0, self.ids)],
            'initial_date': self.date_deadline,
        }
        return action

    def action_feedback(self, feedback=False):
        events = self.mapped('calendar_event_id')
        res = super(MailActivity, self).action_feedback(feedback)
        if feedback:
            feedback_marker = _('Feedback: ')
            feedback_text = tools.html2plaintext(feedback).strip()
            for event in events:
                description = event.description or ''

                # Evita duplicar feedback no evento ao editar o feedback da atividade.
                marker_with_breakline = '\n%s' % feedback_marker
                if marker_with_breakline in description:
                    description = description.rsplit(marker_with_breakline, 1)[0]
                elif description.startswith(feedback_marker):
                    description = ''

                description = '%s%s%s%s' % (
                    description,
                    '\n' if description else '',
                    feedback_marker,
                    feedback_text,
                )
                event.write({'description': description})
        return res

    def unlink_w_meeting(self):
        events = self.mapped('calendar_event_id')
        res = self.unlink()
        events.unlink()
        return res
