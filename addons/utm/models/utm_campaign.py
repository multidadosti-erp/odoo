from odoo import fields, models


class UtmCampaign(models.Model):
    # OLD crm.case.resource.type
    _name = "utm.campaign"
    _description = "UTM Campaign"

    name = fields.Char(
        string="Campaign Name",
        required=True,
        translate=True,
    )

    active = fields.Boolean(
        default=True,
    )
