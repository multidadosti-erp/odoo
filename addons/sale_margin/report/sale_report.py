from odoo import fields, models


class SaleReport(models.Model):
    """Estende o relatório de vendas com informações de margem."""

    _inherit = "sale.report"

    margin = fields.Float("Margin")

    def _query(self, with_clause="", fields={}, groupby="", from_clause=""):
        """Inclui a margem agregada na consulta do relatório de vendas.

        A margem das linhas é convertida pela taxa da moeda do pedido antes
        da soma; quando não há taxa válida, a consulta utiliza ``1.0``.
        """
        fields["margin"] = (
            ", SUM(l.margin / CASE COALESCE(s.currency_rate, 0) WHEN 0 THEN 1.0 ELSE s.currency_rate END) AS margin"
        )
        return super(SaleReport, self)._query(with_clause, fields, groupby, from_clause)
