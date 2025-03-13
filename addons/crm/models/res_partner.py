# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _


class Partner(models.Model):

    _inherit = 'res.partner'

    team_id = fields.Many2one('crm.team', string='Sales Team', oldname='section_id')
    opportunity_ids = fields.One2many('crm.lead', 'partner_id', string='Opportunities', domain=[('type', '=', 'opportunity')])
    meeting_ids = fields.Many2many('calendar.event', 'calendar_event_res_partner_rel', 'res_partner_id', 'calendar_event_id', string='Meetings', copy=False)
    opportunity_count = fields.Integer("Opportunity", compute='_compute_opportunity_count')
    meeting_count = fields.Integer("# Meetings", compute='_compute_meeting_count')

    @api.model
    def default_get(self, fields):
        rec = super(Partner, self).default_get(fields)
        active_model = self.env.context.get('active_model')
        if active_model == 'crm.lead':
            lead = self.env[active_model].browse(self.env.context.get('active_id')).exists()
            if lead:
                rec.update(
                    phone=lead.phone,
                    mobile=lead.mobile,
                    function=lead.function,
                    title=lead.title.id,
                    website=lead.website,
                    street=lead.street,
                    street2=lead.street2,
                    city=lead.city,
                    state_id=lead.state_id.id,
                    country_id=lead.country_id.id,
                    zip=lead.zip,
                )
        return rec

    @api.multi
    def _compute_opportunity_count(self):
        for partner in self:
            operator = 'child_of' if partner.is_company else '='  # the opportunity count should counts the opportunities of this company and all its contacts
            partner.opportunity_count = self.env['crm.lead'].search_count([('partner_id', operator, partner.id), ('type', '=', 'opportunity')])

    def _get_meetings(self):
        """ Adicionado pela Multidados

        Método retorna as reuniões do parceiro.
        Foi adicionado para heranças em outros módulos.

        Returns:
            recordset: Os registros de reuniões associados ao parceiro.
        """
        return self.meeting_ids

    @api.multi
    def _compute_meeting_count(self):
        for partner in self:
            partner.meeting_count = len(partner._get_meetings())

    @api.multi
    def schedule_meeting(self):
        partner_ids = self.ids
        partner_ids.append(self.env.user.partner_id.id)
        action = self.env.ref('calendar.action_calendar_event').read()[0]
        action['context'] = {
            'default_partner_ids': partner_ids,
        }
        return action

    def action_show_opportunity(self):
        """ Open kanban view to display opportunity.
            :return dict: dictionary value for created kanban view
        """
        self.ensure_one()

        action = self.env.ref('crm.crm_lead_opportunities').read()[0]

        form_view = self.env.ref('crm.crm_case_form_view_oppor')
        tree_view = self.env.ref('crm.crm_case_tree_view_leads')

        # Define as views manualmente
        action_views = []
        for view_id, view_type in action['views']:
            if view_type == 'tree':
                view_id = tree_view.id
            elif view_type == 'form':
                view_id = form_view.id
            action_views.append((view_id, view_type))
        action['views'] = action_views

        # the opportunity count should counts the opportunities of this
        # company and all its contacts
        operator = 'child_of' if self.is_company else '='

        # define o domain manualmente
        domain = [('partner_id', operator, self.id)]
        domain += eval(action['domain'])
        action['domain'] = domain

        return action
