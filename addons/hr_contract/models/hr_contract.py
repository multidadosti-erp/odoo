# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import date
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class Employee(models.Model):

    _inherit = "hr.employee"

    manager = fields.Boolean(string='Is a Manager')
    medic_exam = fields.Date(string='Medical Examination Date', groups="hr.group_hr_user")
    vehicle = fields.Char(string='Company Vehicle', groups="hr.group_hr_user")
    contract_ids = fields.One2many('hr.contract', 'employee_id', string='Employee Contracts')
    contract_id = fields.Many2one('hr.contract', compute='_compute_contract_id', string='Current Contract', help='Latest contract of the employee')
    contracts_count = fields.Integer(compute='_compute_contracts_count', string='Contract Count')

    def _compute_contract_id(self):
        """ get the lastest contract """
        Contract = self.env['hr.contract']
        for employee in self:
            employee.contract_id = Contract.search([('employee_id', '=', employee.id)], order='date_start desc', limit=1)

    def _compute_contracts_count(self):
        # read_group as sudo, since contract count is displayed on form view
        contract_data = self.env['hr.contract'].sudo().read_group([('employee_id', 'in', self.ids)], ['employee_id'], ['employee_id'])
        result = dict((data['employee_id'][0], data['employee_id_count']) for data in contract_data)
        for employee in self:
            employee.contracts_count = result.get(employee.id, 0)


class ContractType(models.Model):

    _name = 'hr.contract.type'
    _description = 'Contract Type'
    _order = 'sequence, id'

    name = fields.Char(string='Contract Type', required=True, translate=True)
    sequence = fields.Integer(help="Gives the sequence when displaying a list of Contract.", default=10)


READONLY_STATES = {'open': [('readonly', True)],
                   'pending': [('readonly', True)],
                   'close': [('readonly', True)],
                   'cancel': [('readonly', True)]}

class Contract(models.Model):

    _name = 'hr.contract'
    _description = 'Contract'
    _inherit = ['mail.thread', 'mail.activity.mixin',
        'hr.contract.mixin'] # altreado pela Multidados:
                             #   Conteúdo transferido para o mixin

    name = fields.Char('Contract Reference', required=True)
    active = fields.Boolean(default=True)

    # Alterado pela Multidados:
    # - Transfere campos comuns para o mixin 'hr.contract.mixin'
    # - O mixin agrega os campos principais para serem utilizados na
    #   model de logs (hr.employee.update) e wizard de atualizações
    #   contratuais (update.employee.contract.wizard)

    employee_id = fields.Many2one('hr.employee', readonly=False, states=READONLY_STATES)
    department_id = fields.Many2one('hr.department', readonly=False, states=READONLY_STATES)
    type_id = fields.Many2one('hr.contract.type', readonly=False, states=READONLY_STATES)
    job_id = fields.Many2one('hr.job', readonly=False, states=READONLY_STATES)
    date_start = fields.Date(readonly=False, states=READONLY_STATES)
    date_end = fields.Date(readonly=False, states=READONLY_STATES)

    trial_date_end = fields.Date('End of Trial Period',
        help="End date of the trial period (if there is one).")
    resource_calendar_id = fields.Many2one(
        'resource.calendar', 'Working Schedule',
        default=lambda self: self.env['res.company']._company_default_get().resource_calendar_id.id)
    wage = fields.Monetary(readonly=False, states=READONLY_STATES)
    advantages = fields.Text('Extra Advantages')
    notes = fields.Text(readonly=False, states=READONLY_STATES)
    state = fields.Selection([
        ('draft', 'New'),
        ('open', 'Running'),
        ('pending', 'To Renew'),
        ('close', 'Expired'),
        ('cancel', 'Cancelled')
    ], string='Status', group_expand='_expand_states',
       track_visibility='onchange', help='Status of the contract', default='draft')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.user.company_id)
    # currency_id = fields.Many2one(string="Currency", related='company_id.currency_id', readonly=True)
    permit_no = fields.Char('Work Permit No', related="employee_id.permit_no", readonly=False)
    visa_no = fields.Char('Visa No', related="employee_id.visa_no", readonly=False)
    visa_expire = fields.Date('Visa Expire Date', related="employee_id.visa_expire", readonly=False)
    reported_to_secretariat = fields.Boolean('Social Secretariat',
        help='Green this button when the contract information has been transfered to the social secretariat.')

    # Adicionado pela Multidados:
    schedule_pay = fields.Selection(  # importado parcialmente de 'hr_payroll'
        index=True,                   #   ver mixin 'hr.contract.mixin'
        readonly=False,
        states=READONLY_STATES)


    def _expand_states(self, states, domain, order):
        return [key for key, val in type(self).state.selection]

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        if self.employee_id:
            self.job_id = self.employee_id.job_id
            self.department_id = self.employee_id.department_id
            self.resource_calendar_id = self.employee_id.resource_calendar_id

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        if self.filtered(lambda c: c.date_end and c.date_start > c.date_end):
            raise ValidationError(_('Contract start date must be earlier than contract end date.'))

    @api.model
    def update_state(self):
        self.search([
            ('state', '=', 'open'),
            '|',
            '&',
            ('date_end', '<=', fields.Date.to_string(date.today() + relativedelta(days=7))),
            ('date_end', '>=', fields.Date.to_string(date.today() + relativedelta(days=1))),
            '&',
            ('visa_expire', '<=', fields.Date.to_string(date.today() + relativedelta(days=60))),
            ('visa_expire', '>=', fields.Date.to_string(date.today() + relativedelta(days=1))),
        ]).write({
            'state': 'pending'
        })

        self.search([
            ('state', 'in', ('open', 'pending')),
            '|',
            ('date_end', '<=', fields.Date.to_string(date.today() + relativedelta(days=1))),
            ('visa_expire', '<=', fields.Date.to_string(date.today() + relativedelta(days=1))),
        ]).write({
            'state': 'close'
        })

        return True

    @api.multi
    def _track_subtype(self, init_values):
        self.ensure_one()
        if 'state' in init_values and self.state == 'pending':
            return 'hr_contract.mt_contract_pending'
        elif 'state' in init_values and self.state == 'close':
            return 'hr_contract.mt_contract_close'
        return super(Contract, self)._track_subtype(init_values)
