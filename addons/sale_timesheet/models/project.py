# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class Project(models.Model):
    _inherit = 'project.project'

    sale_order_id = fields.Many2one('sale.order', 'Sales Order', domain="[('partner_id', '=', partner_id)]", readonly=True, copy=False)

    @api.multi
    def action_view_timesheet(self):
        self.ensure_one()
        if self.allow_timesheets:
            return self.action_view_timesheet_plan()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Timesheets of %s') % self.name,
            'domain': [('project_id', '!=', False)],
            'res_model': 'account.analytic.line',
            'view_id': False,
            'view_mode': 'tree,form',
            'view_type': 'form',
            'help': _("""
                <p class="o_view_nocontent_smiling_face">
                    Record timesheets
                </p><p>
                    You can register and track your workings hours by project every
                    day. Every time spent on a project will become a cost and can be re-invoiced to
                    customers if required.
                </p>
            """),
            'limit': 80,
            'context': {
                'default_project_id': self.id,
                'search_default_project_id': [self.id]
            }
        }

    @api.multi
    def action_view_timesheet_plan(self):
        action = self.env.ref('sale_timesheet.project_timesheet_action_client_timesheet_plan').read()[0]
        action['params'] = {
            'project_ids': self.ids,
        }
        action['context'] = {
            'active_id': self.id,
            'active_ids': self.ids,
            'search_default_display_name': self.name,
        }
        return action

    @api.model
    def _map_tasks_default_valeus(self, task):
        defaults = super(Project, self)._map_tasks_default_valeus(task)
        defaults['sale_line_id'] = False
        return defaults


class ProjectTask(models.Model):
    _inherit = "project.task"

    @api.model
    def _get_default_partner(self):
        partner = False
        if 'default_project_id' in self.env.context:  # partner from SO line is prior on one from project
            project_sudo = self.env['project.project'].browse(self.env.context['default_project_id']).sudo()
            partner = project_sudo.sale_order_id.partner_id
        if not partner:
            partner = super(ProjectTask, self)._get_default_partner()
        return partner

    @api.model
    def _default_sale_line_id(self):
        sale_line_id = False
        if self._context.get('default_parent_id'):
            parent_task = self.env['project.task'].browse(self._context['default_parent_id'])
            sale_line_id = parent_task.sale_line_id.id
        return sale_line_id

    sale_line_id = fields.Many2one('sale.order.line', 'Sales Order Item', default=_default_sale_line_id, domain="[('is_service', '=', True), ('order_partner_id', '=', partner_id), ('is_expense', '=', False), ('state', 'in', ['sale', 'done'])]")
    sale_order_id = fields.Many2one('sale.order', 'Sales Order', related="sale_line_id.order_id", store=True, readonly=True)

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        result = super(ProjectTask, self)._onchange_partner_id()
        result = result or {}
        if self.sale_line_id.order_partner_id.commercial_partner_id != self.partner_id.commercial_partner_id:
            self.sale_line_id = False
        if self.partner_id:
            result.setdefault('domain', {})['sale_line_id'] = [('is_service', '=', True), ('is_expense', '=', False), ('order_partner_id', 'child_of', self.partner_id.commercial_partner_id.id), ('state', 'in', ['sale', 'done'])]
        return result

    @api.multi
    def unlink(self):
        if any(task.sale_line_id for task in self):
            raise ValidationError(_('You have to unlink the task from the sale order item in order to delete it.'))
        return super(ProjectTask, self).unlink()

    # ---------------------------------------------------
    # Subtasks
    # ---------------------------------------------------

    @api.model
    def _subtask_implied_fields(self):
        result = super(ProjectTask, self)._subtask_implied_fields()
        return result + ['sale_line_id']

    def _subtask_write_values(self, values):
        result = super(ProjectTask, self)._subtask_write_values(values)
        # changing the partner on a task will reset the sale line of its subtasks
        # if a sale line is not beeing set
        if 'partner_id' in result and 'sale_line_id' not in result:
            result['sale_line_id'] = False
        elif 'sale_line_id' in result:
            result.pop('sale_line_id')
        return result

    # ---------------------------------------------------
    # Actions
    # ---------------------------------------------------

    @api.multi
    def action_view_so(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "views": [[False, "form"]],
            "res_id": self.sale_order_id.id,
            "context": {"create": False, "show_sale": True},
        }

    def rating_get_partner_id(self):
        partner = self.partner_id or self.sale_line_id.order_id.partner_id
        if partner:
            return partner
        return super(ProjectTask, self).rating_get_partner_id()
