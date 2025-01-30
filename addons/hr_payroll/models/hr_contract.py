# -*- coding:utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _


class HrContract(models.Model):
    """
    Employee contract based on the visa, work permits
    allows to configure different Salary structure
    """
    _inherit = 'hr.contract'

    struct_id = fields.Many2one('hr.payroll.structure', string='Salary Structure')

    # Alterado pela Multidados:
    # transfere criação do campo para o módulo 'hr_contract'
    schedule_pay = fields.Selection(selection_add=[
        # ('monthly', 'Monthly'), # transferido para o módulo 'hr_contract'
        ('quarterly', 'Quarterly'),
        ('semi-annually', 'Semi-annually'),
        # ('annually', 'Annually'), # transferido para o módulo 'hr_contract'
        ('weekly', 'Weekly'),
        ('bi-weekly', 'Bi-weekly'),
        ('bi-monthly', 'Bi-monthly'),
    ])
    resource_calendar_id = fields.Many2one(required=True, help="Employee's working schedule.")


    def _recurrency_label_mappings(self):
        """ Adiciona Labels para os novos valores do campo
        Selection 'schedule_pay'.

        Returns:
            dict: Mapeamento do campo 'schedule_pay' com 'recurrency_label'
        """
        return {
            **super(HrContract, self)._recurrency_label_mappings(),
            'quarterly': _("quarter"),
            'semi-annually': _("semester"),
            'weekly': _("week"),
            'bi-weekly': _("fortnight"),
            'bi-monthly': _("two months"),
        }

    @api.multi
    def get_all_structures(self):
        """
        @return: the structures linked to the given contracts, ordered by hierachy (parent=False first,
                 then first level children and so on) and without duplicata
        """
        structures = self.mapped('struct_id')
        if not structures:
            return []
        # YTI TODO return browse records
        return list(set(structures._get_parent_structure().ids))

    @api.multi
    def get_attribute(self, code, attribute):
        return self.env['hr.contract.advantage.template'].search([('code', '=', code)], limit=1)[attribute]

    @api.multi
    def set_attribute_value(self, code, active):
        for contract in self:
            if active:
                value = self.env['hr.contract.advantage.template'].search([('code', '=', code)], limit=1).default_value
                contract[code] = value
            else:
                contract[code] = 0.0


class HrContractAdvandageTemplate(models.Model):
    _name = 'hr.contract.advantage.template'
    _description = "Employee's Advantage on Contract"

    name = fields.Char('Name', required=True)
    code = fields.Char('Code', required=True)
    lower_bound = fields.Float('Lower Bound', help="Lower bound authorized by the employer for this advantage")
    upper_bound = fields.Float('Upper Bound', help="Upper bound authorized by the employer for this advantage")
    default_value = fields.Float('Default value for this advantage')
