# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    service_type = fields.Selection(selection_add=[
        ('timesheet', 'Timesheets on project (one fare per SO/Project)'),
    ])

    @api.onchange('type')
    def _onchange_type(self):
        super(ProductTemplate, self)._onchange_type()
        if self.type == 'service' and not self.invoice_policy:
            self.invoice_policy = 'order'
            self.service_type = 'timesheet'
        elif self.type == 'service' and self.invoice_policy == 'order':
            # self.service_policy = 'ordered_timesheet'
            pass
        elif self.type == 'consu' and not self.invoice_policy and self.service_policy == 'ordered_timesheet':
            self.invoice_policy = 'order'
