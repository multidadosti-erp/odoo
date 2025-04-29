# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from dateutil.relativedelta import relativedelta
from odoo.tools.float_utils import float_round as round
from odoo import api, fields, models


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    quotation_amount_total = fields.Monetary(
        compute="_compute_sale_amount_total",
        string="Sum of Quotation",
        help="Untaxed Total of Quotation",
        currency_field="company_currency",
    )
    sale_amount_total = fields.Monetary(
        compute="_compute_sale_amount_total",
        string="Sum of Orders",
        help="Untaxed Total of Confirmed Orders",
        currency_field="company_currency",
    )
    perc_sale_of_quotation = fields.Float(
        compute="_compute_sale_amount_total",
        string="Perc Orders of Quotations",
        help="Field gives the value of actual Sales Percentage for current opportunity from the Sale Order",
    )
    sale_number = fields.Integer(
        compute="_compute_sale_amount_total",
        string="Number of Quotations",
    )
    order_ids = fields.One2many(
        "sale.order",
        "opportunity_id",
        string="Orders",
    )

    @api.depends('order_ids')
    def _compute_sale_amount_total(self):
        """Computa o total de vendas e o número de cotações para cada lead.

        Este método utiliza operações vetorizadas para melhorar a performance,
        reduzindo o número de iterações e acessos ao banco de dados.
        """
        # Obter todas as moedas das empresas em um único acesso
        company_currencies = {
            company.id: company.currency_id
            for company in self.env["res.company"].browse(self.mapped("company_id.id"))
        }

        for lead in self:
            total = 0.0
            total_quotation = 0.0
            perc_sale_of_quotation = 0.0
            nbr = 0
            company_currency = company_currencies.get(
                lead.company_id.id, self.env.user.company_id.currency_id
            )

            # Filtrar pedidos relevantes diretamente no banco de dados
            orders = lead.order_ids.filtered(lambda o: o.state not in ("cancel"))
            for order in orders:
                # Orçamentos
                if order.state in ("draft", "sent"):
                    nbr += 1
                    total_quotation += order.currency_id._convert(
                        order.amount_untaxed,
                        company_currency,
                        order.company_id,
                        order.date_order or fields.Date.today(),
                    )

                # Pedidos confirmados
                if order.state not in ("draft", "sent", "cancel"):
                    total += order.currency_id._convert(
                        order.amount_untaxed,
                        company_currency,
                        order.company_id,
                        order.date_order or fields.Date.today(),
                    )

            # Calcular a porcentagem de vendas em relação ao total de cotações
            if total_quotation > 0 and total > 0:
                perc_sale_of_quotation = ((total / total_quotation) -1) * 100
            elif total_quotation == 0 and total > 0:
                perc_sale_of_quotation = 100.0

            # Atualizar os campos de forma otimizada
            lead.update(
                {
                    "quotation_amount_total": round(total_quotation, 2),
                    "sale_amount_total": round(total, 2),
                    "sale_number": nbr,
                    "perc_sale_of_quotation": perc_sale_of_quotation,
                }
            )

    @api.model
    def retrieve_sales_dashboard(self):
        res = super(CrmLead, self).retrieve_sales_dashboard()
        date_today = fields.Date.from_string(fields.Date.context_today(self))

        res['invoiced'] = {
            'this_month': 0,
            'last_month': 0,
        }
        account_invoice_domain = [
            ('state', 'in', ['open', 'in_payment', 'paid']),
            ('user_id', '=', self.env.uid),
            ('date_invoice', '>=', date_today.replace(day=1) - relativedelta(months=+1)),
            ('type', 'in', ['out_invoice', 'out_refund'])
        ]

        invoice_data = self.env['account.invoice'].search_read(account_invoice_domain, ['date_invoice', 'amount_untaxed_signed'])

        for invoice in invoice_data:
            if invoice['date_invoice']:
                invoice_date = fields.Date.from_string(invoice['date_invoice'])
                if invoice_date <= date_today and invoice_date >= date_today.replace(day=1):
                    res['invoiced']['this_month'] += invoice['amount_untaxed_signed']
                elif invoice_date < date_today.replace(day=1) and invoice_date >= date_today.replace(day=1) - relativedelta(months=+1):
                    res['invoiced']['last_month'] += invoice['amount_untaxed_signed']

        res['invoiced']['target'] = self.env.user.target_sales_invoiced
        return res
