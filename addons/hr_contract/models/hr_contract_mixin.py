# Parte do conteúdo da model 'hr.contract' dos módulos
# 'hr_contract' e 'hr_payroll' foi transferida para este
# mixin, a fim de facilitar a reutilização dos campos nas
# models de Logs de contrato e Wizard de atualização
# (hr.employee.update) e (update.employee.contract.wizard)

from odoo import models, fields, api, _


class HrContractMixin(models.AbstractModel):
    _name = 'hr.contract.mixin'
    _inherit = ['hr.job.mixin']
    _description = 'Mixin for HR Contract fields for wizards and logs tables'


    # Campos do módulo hr_contract
    employee_id = fields.Many2one(
        'hr.employee',
        string='Employee',
        required=False,
        help="Employee linked to the contract")

    type_id = fields.Many2one(
        "hr.contract.type",
        string="Employee Category",
        required=True,
        default=lambda self: self.env['hr.contract.type'].search([], limit=1),
        help="Type of the contract")

    date_start = fields.Date(
        string="Start Date",
        required=True,
        default=fields.Date.today,
        help="Start date of the contract.")

    date_end = fields.Date(
        string="End Date",
        help="End date of the contract (if it's a fixed-term contract).")

    # Campos do módulo hr_payroll
    schedule_pay = fields.Selection([  # importado parcialmente de 'hr_payroll'
          ('monthly', 'Monthly'),
          ('yearly', 'Yearly'),
        ],
        string="Scheduled Pay",
        required=True,
        default="monthly",
        help="Defines the frequency of the wage payment.")

    wage = fields.Monetary(
        'Wage',
        digits=(16, 2),
        required=True,
        track_visibility="onchange",
        help="Employee's monthly gross wage.")

    notes = fields.Text('Notes')

    recurrency_label = fields.Char(
        string='Recurrency Label',
        compute='_compute_recurrency_label',
        store=False)

    company_id = fields.Many2one('res.company')

    currency_id = fields.Many2one(
        string="Currency",
        related='company_id.currency_id',
        readonly=True)


    # Adicionado pela Multidados:
    def _recurrency_label_mappings(self):
        """ Adicionado pela Multidados:
        Utilizada para obter a string a mostrar no form de contrato,
        gravada no campo computado "recurrency_label".

        Returns:
            dict: Mapeamento do campo 'schedule_pay' com 'recurrency_label'
        """
        return {'monthly': _("month"),
                'yearly': _("year")}

    # Adicionado pela Multidados:
    @api.depends('schedule_pay')
    def _compute_recurrency_label(self):
        """ Adicionado pela Multidados:
        Valor a renderizar na tela informando a recorrência do salário.
        """
        _map = self._recurrency_label_mappings()
        for rec in self:
            rec.recurrency_label = _map.get(rec.schedule_pay, "<???>")
