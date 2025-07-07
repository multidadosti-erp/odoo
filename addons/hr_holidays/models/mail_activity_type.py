from odoo import fields, models


class MailActivityType(models.Model):
    _inherit = "mail.activity.type"

    category = fields.Many2one(selection_add=[('hr_leave', 'HR Leave')])
