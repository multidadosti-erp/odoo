# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, SUPERUSER_ID
from odoo.http import request
from odoo.tools import pycompat


class UtmMixin(models.AbstractModel):
    """
    Mixin class for objects which can be tracked by marketing.
    """

    _name = "utm.mixin"
    _description = "UTM Mixin"

    campaign_id = fields.Many2one(
        "utm.campaign",
        "Campaign",
        ondelete="restrict",
        help="This is a name that helps you keep track of your different campaign efforts, e.g. Fall_Drive, Christmas_Special",
    )

    source_id = fields.Many2one(
        "utm.source",
        "Source",
        ondelete="restrict",
        help="This is the source of the link, e.g. Search Engine, another domain, or name of email list",
    )

    medium_id = fields.Many2one(
        "utm.medium",
        "Medium",
        ondelete="restrict",
        help="This is the method of delivery, e.g. Postcard, Email, or Banner Ad",
        oldname="channel_id",
    )

    def tracking_fields(self):
        # This function cannot be overridden in a model which inherit utm.mixin
        # Limitation by the heritage on AbstractModel
        # record_crm_lead.tracking_fields() will call tracking_fields() from module utm.mixin (if not overridden on crm.lead)
        # instead of the overridden method from utm.mixin.
        # To force the call of overridden method, we use self.env['utm.mixin'].tracking_fields() which respects overridden
        # methods of utm.mixin, but will ignore overridden method on crm.lead
        return [
            # ("URL_PARAMETER", "FIELD_NAME_MIXIN", "NAME_IN_COOKIES")
            ("utm_campaign", "campaign_id", "odoo_utm_campaign"),
            ("utm_source", "source_id", "odoo_utm_source"),
            ("utm_medium", "medium_id", "odoo_utm_medium"),
        ]

    @api.model
    def default_get(self, fields):
        """
        Overrides the default_get method to prefill UTM (Urchin Tracking Module) fields from URL parameters or cookies.

        This method checks if the current user is a salesman (member of 'sales_team.group_sale_salesman').
        If so, UTM fields are ignored to prevent data pollution, unless the request is performed as the superuser.

        For each tracked UTM field:
            - If the field is present in the requested fields, attempts to retrieve its value from cookies.
            - For many2one fields, if the cookie value is a string, searches for a matching record by name or creates one if not found.
            - If a value is found, it is set in the returned defaults.

        Args:
            fields (list): List of field names for which default values are requested.

        Returns:
            dict: Dictionary mapping field names to their default values, with UTM fields populated from cookies or URL parameters when applicable.
        """

        values = super(UtmMixin, self).default_get(fields)

        # We ignore UTM for salemen, except some requests that could be done as superuser_id to bypass access rights.
        if self.env.uid != SUPERUSER_ID and self.env.user.has_group(
            "sales_team.group_sale_salesman"
        ):
            return values

        for url_param, field_name, cookie_name in self.env[
            "utm.mixin"
        ].tracking_fields():

            if field_name in fields:
                field = self._fields[field_name]
                value = False

                if request:
                    # ir_http dispatch saves the url params in a cookie
                    value = request.httprequest.cookies.get(cookie_name)
                # if we receive a string for a many2one, we search/create the id
                if (
                    field.type == "many2one"
                    and isinstance(value, pycompat.string_types)
                    and value
                ):
                    Model = self.env[field.comodel_name]
                    records = Model.search([("name", "=", value)], limit=1)

                    if not records:
                        records = Model.create({"name": value})

                    value = records.id

                if value:
                    values[field_name] = value

        return values
