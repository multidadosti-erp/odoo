from odoo import fields, models


class UtmMedium(models.Model):
    # OLD crm.case.channel
    _name = "utm.medium"
    _description = "UTM Medium"
    _order = "name"

    name = fields.Char(
        string="Channel Name",
        required=True,
    )
    
    active = fields.Boolean(
        default=True,
    )
