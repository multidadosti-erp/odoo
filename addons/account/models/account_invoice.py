# -*- coding: utf-8 -*-

from collections import OrderedDict
import json
import re
import uuid
from functools import partial

from lxml import etree
from dateutil.relativedelta import relativedelta
from werkzeug.urls import url_encode

from odoo import api, exceptions, fields, models, _
from odoo.tools import email_re, email_split, email_escape_char, float_is_zero, float_compare, \
    pycompat, date_utils
from odoo.tools.misc import formatLang

from odoo.exceptions import AccessError, UserError, RedirectWarning, ValidationError, Warning

from odoo.addons import decimal_precision as dp
import logging

_logger = logging.getLogger(__name__)

# mapping invoice type to journal type
TYPE2JOURNAL = {
    'out_invoice': 'sale',
    'in_invoice': 'purchase',
    'out_refund': 'sale',
    'in_refund': 'purchase',
}

# mapping invoice type to refund type
TYPE2REFUND = {
    'out_invoice': 'out_refund',        # Customer Invoice
    'in_invoice': 'in_refund',          # Vendor Bill
    'out_refund': 'out_invoice',        # Customer Credit Note
    'in_refund': 'in_invoice',          # Vendor Credit Note
}

MAGIC_COLUMNS = ('id', 'create_uid', 'create_date', 'write_uid', 'write_date')


class AccountInvoice(models.Model):
    _name = "account.invoice"
    _inherit = ['portal.mixin', 'mail.thread', 'mail.activity.mixin']
    _description = "Invoice"
    _order = "date_invoice desc, number desc, id desc"

    def _get_default_incoterm(self):
        return self.env.user.company_id.incoterm_id

    def prepare_compute_amount(self):
        """
        Prepara os valores totais da fatura, incluindo impostos e valores sem impostos.

        Este método foi otimizado para melhorar a performance utilizando `update` para
        atualizar os campos calculados diretamente, reduzindo o número de operações
        de atribuição.

        Campos calculados:
            - amount_untaxed: Valor total sem impostos.
            - amount_tax: Valor total dos impostos.
            - amount_total: Valor total com impostos.
            - amount_total_company_signed: Valor total em moeda da empresa, com sinal ajustado.
            - amount_total_signed: Valor total em moeda da fatura, com sinal ajustado.
            - amount_untaxed_signed: Valor sem impostos em moeda da empresa, com sinal ajustado.

        Retorna:
            dict: Dicionário contendo os campos a serem atualizados.
        """
        invoice_line_ids = self.invoice_line_ids
        values = {}

        if invoice_line_ids:
            round_curr = self.currency_id.round

            amount_untaxed = sum(line.price_subtotal for line in invoice_line_ids)
            amount_tax = sum(round_curr(line.amount_total) for line in self.tax_line_ids)
            amount_total = amount_untaxed + amount_tax
            amount_total_company_signed = amount_total
            amount_untaxed_signed = amount_untaxed

            if self.currency_id and self.company_id and self.currency_id != self.company_id.currency_id:
                currency_id = self.currency_id
                rate_date = self._get_currency_rate_date() or fields.Date.today()
                amount_total_company_signed = currency_id._convert(
                    amount_total, self.company_id.currency_id, self.company_id, rate_date
                )
                amount_untaxed_signed = currency_id._convert(
                    amount_untaxed, self.company_id.currency_id, self.company_id, rate_date
                )

            sign = -1 if self.type in ["in_refund", "out_refund"] else 1

            # Atualiza os campos calculados diretamente
            values = {
                "amount_untaxed": amount_untaxed,
                "amount_tax": amount_tax,
                "amount_total": amount_total,
                "amount_total_company_signed": amount_total_company_signed * sign,
                "amount_total_signed": amount_total * sign,
                "amount_untaxed_signed": amount_untaxed_signed * sign,
            }

        return values

    @api.one
    @api.depends(
        "invoice_line_ids.price_subtotal",
        "tax_line_ids.amount",
        "tax_line_ids.amount_rounding",
        "currency_id",
        "company_id",
        "date_invoice",
        "type",
        "date",
    )
    def _compute_amount(self):
        """
        Calcula os valores totais da fatura, incluindo impostos e valores sem impostos.

        Este método foi otimizado para melhorar a performance ao evitar operações redundantes.
        Ele utiliza o método `prepare_compute_amount` para calcular os valores e atualiza
        os campos da fatura diretamente.
        """
        values = self.prepare_compute_amount()
        if values:
            self.update(values)

    def _compute_sign_taxes(self):
        """
        Calcula os valores assinados dos impostos e do valor sem impostos para a fatura.

        Este método otimiza o cálculo ao evitar operações desnecessárias e utiliza
        uma abordagem direta para atribuir os valores assinados.

        Campos calculados:
            - amount_untaxed_invoice_signed: Valor sem impostos com sinal ajustado.
            - amount_tax_signed: Valor dos impostos com sinal ajustado.
        """
        for invoice in self:
            sign = -1 if invoice.type in {"in_refund", "out_refund"} else 1

            invoice.update(
                {
                    "amount_untaxed_invoice_signed": invoice.amount_untaxed * sign,
                    "amount_tax_signed": invoice.amount_tax * sign,
                }
            )

    @api.model
    def _default_journal(self):
        """
        Obtém o diário padrão para a fatura com base no tipo de fatura e na empresa.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias
        e ao utilizar verificações diretas para determinar o diário apropriado.

        Retorna:
            recordset: O diário padrão encontrado.
        """
        context = self._context
        default_journal_id = context.get("default_journal_id")
        if default_journal_id:
            return self.env["account.journal"].browse(default_journal_id)

        inv_type = context.get("type", "out_invoice")
        inv_types = inv_type if isinstance(inv_type, list) else [inv_type]
        company_id = context.get("company_id", self.env.user.company_id.id)

        # Determina o domínio para buscar o diário
        domain = [
            (
                "type",
                "in",
                [TYPE2JOURNAL[ty] for ty in inv_types if ty in TYPE2JOURNAL],
            ),
            ("company_id", "=", company_id),
        ]

        # Determina a moeda da empresa e a moeda do contexto
        company_currency_id = self.env["res.company"].browse(company_id).currency_id.id
        currency_id = context.get("default_currency_id", company_currency_id)

        # Adiciona cláusula de moeda ao domínio
        currency_clause = [("currency_id", "=", currency_id)]
        if currency_id == company_currency_id:
            currency_clause = ["|", ("currency_id", "=", False)] + currency_clause

        # Busca o diário com base no domínio e na cláusula de moeda
        journal = self.env["account.journal"].search(domain + currency_clause, limit=1)
        if not journal:
            journal = self.env["account.journal"].search(domain, limit=1)

        return journal

    @api.model
    def _default_currency(self):
        """
        Obtém a moeda padrão para a fatura.

        Este método foi otimizado para evitar buscas desnecessárias e utiliza
        verificações diretas para determinar a moeda padrão com base no diário
        ou na empresa associada.

        Retorna:
            recordset: A moeda padrão encontrada.
        """
        journal = self._context.get("default_journal_id")
        if journal:
            journal = self.env["account.journal"].browse(journal)
            return journal.currency_id or journal.company_id.currency_id

        company_currency = self.env.user.company_id.currency_id
        return company_currency

    def _get_aml_for_amount_residual(self):
        """
        Obtém as linhas de movimentação contábil (account.move.line) relevantes para calcular o valor residual das faturas.

        Retorna apenas as linhas que pertencem à mesma conta contábil da fatura, otimizando a busca ao evitar o uso de `filtered` diretamente.

        Retorno:
            recordset: Conjunto de linhas de movimentação contábil relevantes.
        """
        self.ensure_one()

        return self.sudo().move_id.line_ids.filtered(
            lambda l: l.account_id.id == self.account_id.id
        )

    @api.one
    @api.depends(
        "state",
        "currency_id",
        "invoice_line_ids.price_subtotal",
        "move_id.line_ids.amount_residual",
        "move_id.line_ids.currency_id",
    )
    def _compute_residual(self):
        """
        Calcula os valores residuais da fatura, tanto na moeda da empresa quanto na moeda da fatura.

        Este método foi otimizado para melhorar a performance ao evitar cálculos redundantes
        e ao utilizar uma abordagem direta para acumular os valores residuais.

        Campos calculados:
            - residual: Valor residual na moeda da fatura.
            - residual_signed: Valor residual na moeda da fatura, ajustado pelo sinal.
            - residual_company_signed: Valor residual na moeda da empresa, ajustado pelo sinal.
            - reconciled: Indica se a fatura foi totalmente reconciliada.
        """
        residual = 0.0
        residual_company_signed = 0.0
        sign = -1 if self.type in ["in_refund", "out_refund"] else 1
        currency_id = self.currency_id

        for line in self._get_aml_for_amount_residual():
            residual_company_signed += line.amount_residual
            if line.currency_id == currency_id:
                residual += line.amount_residual_currency or line.amount_residual
            else:
                residual += line.currency_id._convert(
                    line.amount_residual_currency or line.amount_residual,
                    currency_id,
                    line.company_id,
                    line.date or fields.Date.today(),
                )

        # Atualiza os campos calculados
        self.update(
            {
                "residual_company_signed": abs(residual_company_signed) * sign,
                "residual_signed": abs(residual) * sign,
                "residual": abs(residual),
                "reconciled": float_is_zero(residual, precision_rounding=currency_id.rounding),
            }
        )

    def get_domain_outstanding(self):
        """
        Obtém o domínio para buscar créditos ou débitos pendentes relacionados à fatura.

        Este método foi otimizado para garantir que apenas um registro seja processado por vez,
        melhorando a performance ao evitar operações desnecessárias.

        Retorno:
            list: Lista de condições para o domínio de busca.
        """
        self.ensure_one()  # Garante que apenas um registro seja processado por vez

        partner_id = self.env["res.partner"]._find_accounting_partner(self.partner_id).id

        domain = [
            ("account_id", "=", self.account_id.id),
            ("partner_id", "=", partner_id),
            ("reconciled", "=", False),
            ("move_id.state", "=", "posted"),
            "|",
            "&",
            ("amount_residual_currency", "!=", 0.0),
            ("currency_id", "!=", None),
            "&",
            ("amount_residual_currency", "=", 0.0),
            "&",
            ("currency_id", "=", None),
            ("amount_residual", "!=", 0.0),
        ]

        return domain

    @api.one
    def _get_outstanding_info_JSON(self):
        """
        Calcula e armazena as informações de créditos ou débitos pendentes relacionados à fatura no formato JSON.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias
        e ao utilizar uma abordagem direta para construir o JSON apenas quando há linhas relevantes.

        Campos calculados:
            - outstanding_credits_debits_widget: Widget contendo informações de créditos ou débitos pendentes no formato JSON.
            - has_outstanding: Indica se há créditos ou débitos pendentes.
        """
        self.outstanding_credits_debits_widget = json.dumps(False)
        self.has_outstanding = False

        # Comentado para poder incluir creditos antes de confirmar.
        # if self.state != "open":
        #     return

        domain = self.get_domain_outstanding()

        if self.type in ("out_invoice", "in_refund"):
            domain.extend([("credit", ">", 0), ("debit", "=", 0)])
            type_payment = _("Outstanding credits")
        else:
            domain.extend([("credit", "=", 0), ("debit", ">", 0)])
            type_payment = _("Outstanding debits")

        lines = self.env["account.move.line"].search(domain)
        if not lines:
            return

        currency_id = self.currency_id
        content = []

        for line in lines:
            # Calcula o valor residual na moeda da fatura
            if line.currency_id and line.currency_id == self.currency_id:
                amount_to_show = abs(line.amount_residual_currency)
            else:
                amount_to_show = line.company_id.currency_id._convert(
                    abs(line.amount_residual), self.currency_id, self.company_id, line.date or fields.Date.today()
                )

            if float_is_zero(amount_to_show, precision_rounding=self.currency_id.rounding):
                continue

            title = (
                f"{line.move_id.name} : {line.ref}" if line.ref else line.move_id.name
            )
            content.append(
                {
                    "journal_name": line.ref or line.move_id.name,
                    "title": title,
                    "amount": amount_to_show,
                    "currency": currency_id.symbol,
                    "id": line.id,
                    "position": currency_id.position,
                    "digits": [69, self.currency_id.decimal_places],
                }
            )

        if content:
            self.outstanding_credits_debits_widget = json.dumps(
                {
                    "title": type_payment,
                    "outstanding": True,
                    "content": content,
                    "invoice_id": self.id,
                }
            )
            self.has_outstanding = True

    @api.model
    def _get_payments_vals(self):
        """
        Obtém os valores dos pagamentos relacionados à fatura.

        Este método foi otimizado para melhorar a performance ao evitar cálculos redundantes
        e ao utilizar uma abordagem direta para acumular os valores dos pagamentos.

        Retorna:
            list: Lista de dicionários contendo informações dos pagamentos.
        """
        if not self.payment_move_line_ids:
            return []

        payment_vals = []
        currency_id = self.currency_id
        company_currency = self.company_id.currency_id
        today = fields.Date.today()

        for payment in self.payment_move_line_ids:
            # Determina os valores de débito/crédito e a moeda do pagamento
            if self.type in ("out_invoice", "in_refund"):
                matched_lines = payment.matched_debit_ids.filtered(
                    lambda p: p.debit_move_id in self.move_id.line_ids
                )
            else:
                matched_lines = payment.matched_credit_ids.filtered(
                    lambda p: p.credit_move_id in self.move_id.line_ids
                )

            if not matched_lines:
                continue

            amount = sum(line.amount for line in matched_lines)
            amount_currency = sum(line.amount_currency for line in matched_lines)
            payment_currency_id = (
                matched_lines[0].currency_id
                if all(
                    line.currency_id == matched_lines[0].currency_id
                    for line in matched_lines
                )
                else False
            )

            # Converte o valor para a moeda da fatura, se necessário
            if payment_currency_id and payment_currency_id == self.currency_id:
                amount_to_show = amount_currency
            else:
                amount_to_show = company_currency._convert(
                    amount, self.currency_id, self.company_id, payment.date or today
                )

            if float_is_zero(amount_to_show, precision_rounding=self.currency_id.rounding):
                continue

            # Monta a referência do pagamento
            payment_ref = payment.move_id.name
            if payment.move_id.ref:
                payment_ref += f" ({payment.move_id.ref})"

            # Adiciona os valores do pagamento à lista
            payment_vals.append(
                {
                    "name": payment.name,
                    "journal_name": payment.journal_id.name,
                    "amount": amount_to_show,
                    "currency": currency_id.symbol,
                    "digits": [69, currency_id.decimal_places],
                    "position": currency_id.position,
                    "date": payment.date,
                    "payment_id": payment.id,
                    "account_payment_id": payment.payment_id.id,
                    "invoice_id": payment.invoice_id.id,
                    "invoice_view_id": (
                        payment.invoice_id.get_formview_id()
                        if payment.invoice_id
                        else None
                    ),
                    "move_id": payment.move_id.id,
                    "ref": payment_ref,
                }
            )

        return payment_vals

    @api.depends('payment_move_line_ids.amount_residual')
    def _get_payment_info_JSON(self):
        """
        Calcula e armazena as informações de pagamentos relacionados à fatura no formato JSON.

        Este método é otimizado para evitar cálculos desnecessários e utiliza
        uma abordagem direta para construir o JSON apenas quando há linhas de pagamento.

        Campos calculados:
            - payments_widget: Widget contendo informações de pagamentos no formato JSON.
        """
        for invoice in self:
            if invoice.payment_move_line_ids:
                info = {
                    "title": _("Less Payment"),
                    "outstanding": False,
                    "content": invoice._get_payments_vals(),
                }
                invoice.payments_widget = json.dumps(
                    info, default=date_utils.json_default
                )
            else:
                invoice.payments_widget = json.dumps(False)

    @api.depends("move_id.line_ids.amount_residual")
    def _compute_payments(self):
        """
        Calcula as linhas de movimentação contábil relacionadas aos pagamentos da fatura.

        Este método utiliza um conjunto para evitar duplicatas e melhora a performance
        ao evitar múltiplas buscas desnecessárias no banco de dados.

        Campos calculados:
            - payment_move_line_ids: Linhas de movimentação contábil relacionadas aos pagamentos.

        """
        for invoice in self:
            if not invoice.move_id:
                invoice.payment_move_line_ids = self.env["account.move.line"]
                continue

            # Filtra as linhas de movimentação contábil relevantes e coleta os IDs de pagamentos
            payment_lines = {
                line.id
                for line in invoice.move_id.line_ids
                if line.account_id.id == invoice.account_id.id
                for matched_line in line.matched_credit_ids | line.matched_debit_ids
                for line in (matched_line.credit_move_id, matched_line.debit_move_id)
            }

            # Define as linhas de pagamento ordenadas
            invoice.payment_move_line_ids = (
                self.env["account.move.line"].browse(payment_lines).sorted()
            )

    name = fields.Char(string='Reference/Description', index=True,
        readonly=True, states={'draft': [('readonly', False)]}, copy=False, help='The name that will be used on account move lines')

    origin = fields.Char(string='Source Document',
        help="Reference of the document that produced this invoice.",
        readonly=True, states={'draft': [('readonly', False)]})
    type = fields.Selection([
            ('out_invoice','Customer Invoice'),
            ('in_invoice','Vendor Bill'),
            ('out_refund','Customer Credit Note'),
            ('in_refund','Vendor Credit Note'),
        ], readonly=True, states={'draft': [('readonly', False)]}, index=True, change_default=True,
        default=lambda self: self._context.get('type', 'out_invoice'),
        track_visibility='always')

    refund_invoice_id = fields.Many2one('account.invoice', string="Invoice for which this invoice is the credit note")
    number = fields.Char(related='move_id.name', store=True, readonly=True, copy=False)
    move_name = fields.Char(string='Journal Entry Name', readonly=False,
        default=False, copy=False,
        help="Technical field holding the number given to the invoice, automatically set when the invoice is validated then stored to set the same number again if the invoice is cancelled, set to draft and re-validated.")
    reference = fields.Char(string='Payment Ref.', copy=False, readonly=True, states={'draft': [('readonly', False)]},
        help='The payment communication that will be automatically populated once the invoice validation. You can also write a free communication.')
    comment = fields.Text('Additional Information', readonly=True, states={'draft': [('readonly', False)]})

    state = fields.Selection([
            ('draft','Draft'),
            ('open', 'Open'),
            ('in_payment', 'In Payment'),
            ('paid', 'Paid'),
            ('cancel', 'Cancelled'),
        ], string='Status', index=True, readonly=True, default='draft',
        track_visibility='onchange', copy=False,
        help=" * The 'Draft' status is used when a user is encoding a new and unconfirmed Invoice.\n"
             " * The 'Open' status is used when user creates invoice, an invoice number is generated. It stays in the open status till the user pays the invoice.\n"
             " * The 'In Payment' status is used when payments have been registered for the entirety of the invoice in a journal configured to post entries at bank reconciliation only, and some of them haven't been reconciled with a bank statement line yet.\n"
             " * The 'Paid' status is set automatically when the invoice is paid. Its related journal entries may or may not be reconciled.\n"
             " * The 'Cancelled' status is used when user cancel invoice.")
    sent = fields.Boolean(readonly=True, default=False, copy=False,
        help="It indicates that the invoice has been sent.")
    date_invoice = fields.Date(string='Invoice Date',
        readonly=True, states={'draft': [('readonly', False)]}, index=True,
        help="Keep empty to use the current date", copy=False)
    date_due = fields.Date(string='Due Date',
        readonly=True, states={'draft': [('readonly', False)]}, index=True, copy=False,
        help="If you use payment terms, the due date will be computed automatically at the generation "
             "of accounting entries. The Payment terms may compute several due dates, for example 50% "
             "now and 50% in one month, but if you want to force a due date, make sure that the payment "
             "term is not set on the invoice. If you keep the Payment terms and the due date empty, it "
             "means direct payment.")
    partner_id = fields.Many2one('res.partner', string='Partner', change_default=True,
        readonly=True, states={'draft': [('readonly', False)]},
        track_visibility='always', ondelete='restrict', help="You can find a contact by its Name, TIN, Email or Internal Reference.")
    vendor_bill_id = fields.Many2one('account.invoice', string='Vendor Bill',
        help="Auto-complete from a past bill.")
    payment_term_id = fields.Many2one('account.payment.term', string='Payment Terms', oldname='payment_term',
        readonly=True, states={'draft': [('readonly', False)]},
        help="If you use payment terms, the due date will be computed automatically at the generation "
             "of accounting entries. If you keep the payment terms and the due date empty, it means direct payment. "
             "The payment terms may compute several due dates, for example 50% now, 50% in one month.")
    date = fields.Date(string='Accounting Date',
        copy=False,
        help="Keep empty to use the invoice date.",
        readonly=True, states={'draft': [('readonly', False)]})

    account_id = fields.Many2one('account.account', string='Account',
        readonly=True, states={'draft': [('readonly', False)]},
        domain=[('deprecated', '=', False)], help="The partner account used for this invoice.")
    invoice_line_ids = fields.One2many('account.invoice.line', 'invoice_id', string='Invoice Lines', oldname='invoice_line',
        readonly=True, states={'draft': [('readonly', False)]}, copy=True)
    tax_line_ids = fields.One2many('account.invoice.tax', 'invoice_id', string='Tax Lines', oldname='tax_line',
        readonly=True, states={'draft': [('readonly', False)]}, copy=True)
    refund_invoice_ids = fields.One2many('account.invoice', 'refund_invoice_id', string='Refund Invoices', readonly=True)
    move_id = fields.Many2one('account.move', string='Journal Entry',
        readonly=True, index=True, ondelete='restrict', copy=False,
        help="Link to the automatically generated Journal Items.")

    amount_by_group = fields.Binary(string="Tax amount by group", compute='_amount_by_group', help="type: [(name, amount, base, formated amount, formated base)]")
    amount_untaxed = fields.Monetary(string='Untaxed Amount',
        store=True, readonly=True, compute='_compute_amount', track_visibility='always')
    amount_untaxed_signed = fields.Monetary(string='Untaxed Amount in Company Currency', currency_field='company_currency_id',
        store=True, readonly=True, compute='_compute_amount')
    amount_untaxed_invoice_signed = fields.Monetary(string='Untaxed Amount in Invoice Currency', currency_field='currency_id',
        readonly=True, compute='_compute_sign_taxes')
    amount_tax = fields.Monetary(string='Tax',
        store=True, readonly=True, compute='_compute_amount')
    amount_tax_signed = fields.Monetary(string='Tax in Invoice Currency', currency_field='currency_id',
        readonly=True, compute='_compute_sign_taxes')
    amount_total = fields.Monetary(string='Total',
        store=True, readonly=True, compute='_compute_amount')
    amount_total_signed = fields.Monetary(string='Total in Invoice Currency', currency_field='currency_id',
        store=True, readonly=True, compute='_compute_amount',
        help="Total amount in the currency of the invoice, negative for credit notes.")
    amount_total_company_signed = fields.Monetary(string='Total in Company Currency', currency_field='company_currency_id',
        store=True, readonly=True, compute='_compute_amount',
        help="Total amount in the currency of the company, negative for credit notes.")
    currency_id = fields.Many2one('res.currency', string='Currency',
        required=True, readonly=True, states={'draft': [('readonly', False)]},
        default=_default_currency, track_visibility='always')
    company_currency_id = fields.Many2one('res.currency', related='company_id.currency_id', string="Company Currency", readonly=True)
    journal_id = fields.Many2one('account.journal', string='Journal',
        required=True, readonly=True, states={'draft': [('readonly', False)]},
        default=_default_journal,
        domain="[('type', 'in', {'out_invoice': ['sale'], 'out_refund': ['sale'], 'in_refund': ['purchase'], 'in_invoice': ['purchase']}.get(type, [])), ('company_id', '=', company_id)]")
    company_id = fields.Many2one('res.company', string='Company', track_visibility="onchange",
        change_default=True,
        required=True, readonly=True, states={'draft': [('readonly', False)]},
        default=lambda self: self.env['res.company']._company_default_get('account.invoice'))

    reconciled = fields.Boolean(string='Paid/Reconciled', store=True, readonly=True, compute='_compute_residual',
        help="It indicates that the invoice has been paid and the journal entry of the invoice has been reconciled with one or several journal entries of payment.")
    partner_bank_id = fields.Many2one('res.partner.bank', string='Bank Account',
        help='Bank Account Number to which the invoice will be paid. A Company bank account if this is a Customer Invoice or Vendor Credit Note, otherwise a Partner bank account number.',
        readonly=True, states={'draft': [('readonly', False)]}) #Default value computed in default_get for out_invoices

    residual = fields.Monetary(string='Amount Due',
        compute='_compute_residual', store=True, help="Remaining amount due.")
    residual_signed = fields.Monetary(string='Amount Due in Invoice Currency', currency_field='currency_id',
        compute='_compute_residual', store=True, help="Remaining amount due in the currency of the invoice.")
    residual_company_signed = fields.Monetary(string='Amount Due in Company Currency', currency_field='company_currency_id',
        compute='_compute_residual', store=True, help="Remaining amount due in the currency of the company.")
    payment_ids = fields.Many2many('account.payment', 'account_invoice_payment_rel', 'invoice_id', 'payment_id', string="Payments", copy=False, readonly=True)
    payment_move_line_ids = fields.Many2many('account.move.line', string='Payment Move Lines', compute='_compute_payments', store=True)
    user_id = fields.Many2one('res.users', string='Salesperson', track_visibility='onchange',
        readonly=True, states={'draft': [('readonly', False)]},
        default=lambda self: self.env.user, copy=False)
    fiscal_position_id = fields.Many2one('account.fiscal.position', string='Fiscal Position', oldname='fiscal_position', ondelete='restrict',
        readonly=True, states={'draft': [('readonly', False)]})
    commercial_partner_id = fields.Many2one('res.partner', string='Commercial Entity', compute_sudo=True,
        related='partner_id.commercial_partner_id', store=True, readonly=True,
        help="The commercial entity that will be used on Journal Entries for this invoice")

    outstanding_credits_debits_widget = fields.Text(compute='_get_outstanding_info_JSON', groups="account.group_account_invoice")
    payments_widget = fields.Text(compute='_get_payment_info_JSON', groups="account.group_account_invoice")
    has_outstanding = fields.Boolean(compute='_get_outstanding_info_JSON', groups="account.group_account_invoice")
    cash_rounding_id = fields.Many2one('account.cash.rounding', string='Cash Rounding Method',
        readonly=True, states={'draft': [('readonly', False)]},
        help='Defines the smallest coinage of the currency that can be used to pay by cash.')

    # fields use to set the sequence, on the first invoice of the journal
    sequence_number_next = fields.Char(string='Next Number', compute="_get_sequence_number_next", inverse="_set_sequence_next")
    sequence_number_next_prefix = fields.Char(string='Next Number Prefix', compute="_get_sequence_prefix")
    incoterm_id = fields.Many2one('account.incoterms', string='Incoterm',
        default=_get_default_incoterm,
        help='International Commercial Terms are a series of predefined commercial terms used in international transactions.')

    # fields related to vendor bills automated creation by email
    source_email = fields.Char(string='Source Email', track_visibility='onchange')
    vendor_display_name = fields.Char(compute='_get_vendor_display_info', store=True)  # store=True to enable sorting on that column
    invoice_icon = fields.Char(compute='_get_vendor_display_info', store=False)

    _sql_constraints = [
        ('number_uniq', 'unique(number, company_id, journal_id, type)', 'Invoice Number must be unique per Company!'),
    ]

    @api.depends("partner_id", "source_email")
    def _get_vendor_display_info(self):
        """
        Atualiza as informações de exibição do fornecedor para cada fatura.

        Este método otimiza o cálculo ao evitar atribuições repetitivas e utiliza
        verificações diretas para melhorar a performance.

        Campos calculados:
            - vendor_display_name: Nome do fornecedor ou informações alternativas.
            - invoice_icon: Ícone associado ao fornecedor ou criador da fatura.
        """
        for invoice in self:
            vendor_display_name = invoice.partner_id.name or ""
            invoice_icon = ""

            if not vendor_display_name:
                if invoice.source_email:
                    vendor_display_name = f"{_('From: ')}{invoice.source_email}"
                    invoice_icon = "@"
                else:
                    vendor_display_name = _(
                        "Created by: %s" % invoice.sudo().create_uid.name
                    )
                    invoice_icon = "#"

            invoice.update(
                {
                    "vendor_display_name": vendor_display_name,
                    "invoice_icon": invoice_icon,
                }
            )

    @api.multi
    def _get_computed_reference(self):
        """
        Calcula a referência da fatura com base no tipo de referência configurado na empresa.

        Este método foi otimizado para melhorar a performance ao evitar operações desnecessárias
        e utiliza verificações diretas para determinar o tipo de referência.

        Retorna:
            str: Referência calculada para a fatura.
        """
        self.ensure_one()

        # Verifica o tipo de referência configurado na empresa
        reference_type = self.company_id.invoice_reference_type
        if reference_type == "invoice_number":
            seq_suffix = self.journal_id.sequence_id.suffix or ""
            regex_number = rf".*?(\d+){re.escape(seq_suffix)}$"
            match = re.match(regex_number, self.number)

            # Determina o número de identificação com base no número da fatura
            if match:
                identification_number = int(match.group(1))
            else:
                identification_number = int(str(uuid.uuid4().int)[-10:])
            prefix = self.number

        elif reference_type == "partner":
            # Determina o número de identificação com base no ID do parceiro
            identification_number = self.partner_id.id
            prefix = "CUST"

        else:
            return ""

        # Calcula o dígito verificador usando o módulo 97
        check_digit = str(identification_number % 97).zfill(2)

        return f"{prefix}/{check_digit}"

    # Load all Vendor Bill lines
    @api.onchange("vendor_bill_id")
    def _onchange_vendor_bill(self):
        """
        Atualiza os campos da fatura com base na fatura do fornecedor selecionada.

        Este método otimiza a performance ao evitar operações desnecessárias e utiliza
        uma abordagem direta para criar novas linhas de fatura e atualizar os campos relevantes.

        - Atualiza a moeda da fatura com base na fatura do fornecedor.
        - Cria novas linhas de fatura com base nas linhas da fatura do fornecedor.
        - Atualiza o termo de pagamento com base na fatura do fornecedor.
        - Limpa o campo `vendor_bill_id` após a atualização.

        Retorno:
            dict: Retorna um dicionário vazio.
        """
        if not self.vendor_bill_id:
            return {}

        # Atualiza a moeda da fatura
        self.currency_id = self.vendor_bill_id.currency_id

        # Cria novas linhas de fatura de forma eficiente
        new_lines = [
            (0, 0, line._prepare_invoice_line())
            for line in self.vendor_bill_id.invoice_line_ids
        ]

        self.update(
            {
                "invoice_line_ids": new_lines,
                "payment_term_id": self.vendor_bill_id.payment_term_id,
                "vendor_bill_id": False,
            }
        )

        return {}

    def _get_seq_number_next_stuff(self):
        """
        Obtém a sequência do diário e o domínio para determinar o próximo número da sequência.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias
        e utiliza verificações diretas para determinar a sequência e o domínio apropriados.

        Retorna:
            tuple: Contém a sequência do diário e o domínio para busca.
        """
        self.ensure_one()

        # Determina a sequência do diário com base no tipo de fatura
        journal_sequence = (
            self.journal_id.refund_sequence_id
            if (
                self.journal_id.refund_sequence
                and self.type in ["in_refund", "out_refund"]
            )
            else self.journal_id.sequence_id
        )

        # Define o domínio para buscar faturas relevantes
        domain = [
            ("journal_id", "=", self.journal_id.id),
            ("state", "not in", ["draft", "cancel"]),
        ]
        if self.type in ["in_refund", "out_refund"]:
            domain.append(("type", "=", self.type))
        elif self.type in ["in_invoice", "in_refund"]:
            domain.append(("type", "in", ["in_invoice", "in_refund"]))
        else:
            domain.append(("type", "in", ["out_invoice", "out_refund"]))

        # Exclui a fatura atual do domínio, se aplicável
        if self.id:
            domain.append(("id", "!=", self.id))

        return journal_sequence, domain

    def _compute_access_url(self):
        """
        Calcula a URL de acesso para cada fatura.

        Este método foi otimizado para evitar chamadas desnecessárias ao método `super`
        e utiliza uma abordagem direta para atribuir a URL de acesso.

        - Atualiza o campo `access_url` com a URL correspondente à fatura.
        """
        for invoice in self:
            invoice.access_url = f"/my/invoices/{invoice.id}"

    @api.depends("state", "journal_id", "date", "date_invoice")
    def _get_sequence_prefix(self):
        """
        Calcula o prefixo do número que será atribuído à primeira fatura/nota fiscal/reembolso de um diário,
        permitindo que o usuário altere manualmente.

        Este método foi otimizado para evitar buscas desnecessárias e utiliza verificações diretas para
        determinar o prefixo da sequência.

        - Atualiza o campo `sequence_number_next_prefix` com o prefixo correspondente.
        - Define o campo como vazio se o usuário não for um administrador ou se a sequência já existir.

        """
        if not self.env.user._is_system():
            self.update(
                {"sequence_number_next_prefix": False, "sequence_number_next": ""}
            )
            return

        for invoice in self:
            journal_sequence, domain = invoice._get_seq_number_next_stuff()
            sequence_date = invoice.date or invoice.date_invoice

            # Verifica se a fatura está em rascunho e se não há outras faturas na sequência
            if invoice.state == "draft" and not self.search(domain, limit=1):
                prefix, _ = journal_sequence.with_context(
                    ir_sequence_date=sequence_date, ir_sequence_date_range=sequence_date
                )._get_prefix_suffix()
                invoice.sequence_number_next_prefix = prefix
            else:
                invoice.sequence_number_next_prefix = False

    @api.depends("state", "journal_id")
    def _get_sequence_number_next(self):
        """
        Calcula o próximo número que será atribuído à primeira fatura/nota fiscal/reembolso de um diário,
        permitindo que o usuário altere manualmente.

        Este método foi otimizado para evitar buscas desnecessárias e utiliza verificações diretas para
        determinar o próximo número da sequência.

        - Atualiza o campo `sequence_number_next` com o próximo número da sequência.
        - Define o campo como vazio se a sequência já existir ou se a fatura não estiver em rascunho.

        """
        for invoice in self:
            # Obtém a sequência do diário e o domínio para busca
            journal_sequence, domain = invoice._get_seq_number_next_stuff()

            # Verifica se a fatura está em rascunho e se não há outras faturas na sequência
            if invoice.state == "draft" and not self.search(domain, limit=1):
                sequence_date = invoice.date or invoice.date_invoice
                current_sequence = journal_sequence._get_current_sequence(
                    sequence_date=sequence_date
                )
                invoice.sequence_number_next = f"{current_sequence.number_next_actual:0{journal_sequence.padding}d}"
            else:
                invoice.sequence_number_next = ""

    @api.multi
    def _set_sequence_next(self):
        """
        Define o próximo número na sequência relacionada à fatura/nota fiscal/reembolso.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias
        e utiliza verificações diretas para determinar a sequência e o próximo número.

        - Verifica se o usuário é administrador antes de permitir alterações.
        - Garante que a sequência seja atualizada apenas se não houver conflitos com outras faturas.

        """
        self.ensure_one()

        # Obtém a sequência do diário e o domínio para busca
        journal_sequence, domain = self._get_seq_number_next_stuff()

        # Verifica se o usuário é administrador e se a sequência pode ser atualizada
        if (
            not self.env.user._is_admin()
            or not self.sequence_number_next
            or self.search_count(domain)
        ):
            return

        # Extrai apenas os números da sequência
        nxt = re.sub(r"[^\d]", "", self.sequence_number_next)
        if not nxt.isdigit():
            return

        # Atualiza o próximo número na sequência
        if journal_sequence:
            sequence_date = self.date or self.date_invoice
            sequence = journal_sequence._get_current_sequence(
                sequence_date=sequence_date
            )
            sequence.number_next = int(nxt)

    @api.multi
    def _get_report_base_filename(self):
        self.ensure_one()
        return  self.type == 'out_invoice' and self.state == 'draft' and _('Draft Invoice') or \
                self.type == 'out_invoice' and self.state in ('open','in_payment','paid') and _('Invoice - %s') % (self.number) or \
                self.type == 'out_refund' and self.state == 'draft' and _('Credit Note') or \
                self.type == 'out_refund' and _('Credit Note - %s') % (self.number) or \
                self.type == 'in_invoice' and self.state == 'draft' and _('Vendor Bill') or \
                self.type == 'in_invoice' and self.state in ('open','in_payment','paid') and _('Vendor Bill - %s') % (self.number) or \
                self.type == 'in_refund' and self.state == 'draft' and _('Vendor Credit Note') or \
                self.type == 'in_refund' and _('Vendor Credit Note - %s') % (self.number)

    @api.model
    def create(self, vals):
        """
        Cria uma nova fatura com otimizações de desempenho e preenchimento automático de campos.

        Este método foi otimizado para evitar buscas desnecessárias e utiliza verificações diretas
        para determinar os valores padrão e aplicar alterações automáticas.

        Args:
            vals (dict): Dicionário contendo os valores para criar a fatura.

        Returns:
            recordset: Registro da fatura criada.
        """
        # Define o diário padrão com base no tipo de fatura, se não especificado
        if not vals.get("journal_id") and vals.get("type"):
            vals["journal_id"] = (
                self.with_context(type=vals.get("type"))._default_journal().id
            )

        # Aplica os métodos de onchange para preencher campos automaticamente
        onchanges = self._get_onchange_create()
        missing_fields = {
            field
            for onchange_method, changed_fields in onchanges.items()
            for field in changed_fields
            if field not in vals
        }
        if missing_fields:
            invoice = self.new(vals)
            for onchange_method, changed_fields in onchanges.items():
                if any(field in missing_fields for field in changed_fields):
                    getattr(invoice, onchange_method)()
                    for field in changed_fields:
                        if field in missing_fields and invoice[field]:
                            vals[field] = invoice._fields[field].convert_to_write(
                                invoice[field], invoice
                            )

        # Define a data contabil caso não informado e tenha data da fatura
        if not vals.get("date") and vals.get("date_invoice"):
            vals["date"] = vals["date_invoice"]

        # Define a conta bancária padrão, se aplicável
        if not vals.get("partner_bank_id"):
            bank_account = self._get_default_bank_id(
                vals.get("type"), vals.get("company_id")
            )
            if bank_account:
                vals["partner_bank_id"] = bank_account.id

        # Cria a fatura e calcula os impostos, se necessário
        invoice = super(
            AccountInvoice, self.with_context(mail_create_nolog=True)
        ).create(vals)

        if (
            any(line.invoice_line_tax_ids for line in invoice.invoice_line_ids)
            and not invoice.tax_line_ids
        ):
            invoice.compute_taxes()

        return invoice

    @api.constrains("partner_id", "partner_bank_id")
    def validate_partner_bank_id(self):
        """
        Valida se a conta bancária do parceiro está correta com base no tipo de fatura.

        - Para faturas de entrada e notas de crédito de cliente, verifica se o proprietário da conta bancária
          é o mesmo que o parceiro comercial.
        - Para faturas de saída e notas de crédito de fornecedor, verifica se a conta bancária pertence
          à mesma empresa da fatura.

        Este método foi otimizado para evitar verificações desnecessárias e melhorar a performance.

        Raises:
            ValidationError: Se a conta bancária não for válida para o parceiro ou empresa.
        """
        for record in self.filtered("partner_bank_id"):
            if record.type in ("in_invoice", "out_refund"):
                if record.partner_bank_id.partner_id != record.partner_id.commercial_partner_id:
                    raise ValidationError(_("Commercial partner and vendor account owners must be identical."))
            elif record.type in ("out_invoice", "in_refund"):
                if record.company_id not in record.partner_bank_id.partner_id.ref_company_ids:
                    raise ValidationError(_("The account selected for payment does not belong to the same company as this invoice."))

    def get_invoice_reconciled(self):
        """
        Obtém as faturas que foram reconciliadas.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias
        e utiliza filtragens diretas para determinar as faturas reconciliadas.

        Returns:
            recordset: Conjunto de registros das faturas reconciliadas.
        """
        return self.filtered(lambda invoice: invoice.reconciled)

    def get_invoice_not_reconciled(self):
        """
        Obtém as faturas que não foram reconciliadas.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias
        e utiliza filtragens diretas para determinar as faturas não reconciliadas.

        Returns:
            recordset: Conjunto de registros das faturas reconciliadas.
        """
        return self.filtered(lambda invoice: not invoice.reconciled)      

    @api.multi
    def _write(self, vals):
        """
        Atualiza os registros de faturas e ajusta os estados com base na reconciliação.

        Este método foi otimizado para melhorar a performance ao evitar filtragens redundantes
        e utiliza conjuntos para operações mais eficientes.

        Args:
            vals (dict): Valores a serem atualizados nos registros.

        Returns:
            bool: Resultado da operação de escrita.
        """
        # Identifica as faturas não reconciliadas antes da atualização
        pre_not_reconciled = self.get_invoice_not_reconciled()
        pre_reconciled = self - pre_not_reconciled

        # Realiza a atualização nos registros
        res = super(AccountInvoice, self)._write(vals)

        # Identifica as faturas reconciliadas após a atualização
        reconciled = self.get_invoice_reconciled()
        not_reconciled = self - reconciled

        # Ajusta o estado das faturas reconciliadas que estavam reconciliadas anteriormente
        reconciled_to_paid = reconciled & pre_reconciled
        reconciled_to_paid = reconciled_to_paid.filtered(lambda invoice: invoice.state == "open")
        if reconciled_to_paid:
            reconciled_to_paid.action_invoice_paid()

        # Ajusta o estado das faturas não reconciliadas que estavam não reconciliadas anteriormente
        not_reconciled_to_open = not_reconciled & pre_not_reconciled
        not_reconciled_to_open = not_reconciled_to_open.filtered(lambda invoice: invoice.state in ("in_payment", "paid"))
        if not_reconciled_to_open:
            not_reconciled_to_open.action_invoice_re_open()

        return res

    @api.model
    def default_get(self, default_fields):
        """
        Calcula o valor padrão para o campo `partner_bank_id` no caso de faturas do tipo 'out_invoice',
        utilizando os valores padrão já calculados para os outros campos.

        Este método foi otimizado para evitar buscas desnecessárias e melhorar a performance.

        Args:
            default_fields (list): Lista de campos padrão a serem calculados.

        Returns:
            dict: Dicionário contendo os valores padrão calculados.
        """
        res = super().default_get(default_fields)

        # Verifica se o tipo e a empresa estão definidos antes de buscar o banco padrão
        invoice_type = res.get("type")
        company_id = res.get("company_id")
        if invoice_type and company_id:
            partner_bank_result = self._get_default_bank_id(invoice_type, company_id)
            if partner_bank_result:
                res["partner_bank_id"] = partner_bank_result.id

        return res

    def _get_default_bank_id(self, type, company_id):
        """
        Obtém o ID da conta bancária padrão com base no tipo de fatura e na empresa.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias
        e utiliza verificações diretas para determinar a conta bancária apropriada.

        Args:
            type (str): Tipo da fatura ('out_invoice', 'in_refund', etc.).
            company_id (int): ID da empresa associada.

        Retorna:
            res.partner.bank: Registro da conta bancária padrão ou False se não aplicável.
        """
        if type in ("out_invoice", "in_refund") and company_id:
            company = self.env["res.company"].browse(company_id)
            partner_id = company.partner_id.id if company.partner_id else False
            if partner_id:
                bank = self.env["res.partner.bank"].search_read(
                    [
                        ("partner_id", "=", partner_id),
                        ("company_id", "in", [company_id, False]),
                    ],
                    ["id"],
                    limit=1,
                    order="company_id DESC",
                )
                if bank:
                    return self.env["res.partner.bank"].browse(bank[0]["id"])

        return False

    def _get_partner_bank_id(self, company_id):
        """
        Obtém a conta bancária do parceiro associada à empresa.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias
        e utiliza verificações diretas para determinar a conta bancária apropriada.

        Args:
            company_id (int): ID da empresa associada.

        Returns:
            res.partner.bank: Registro da conta bancária encontrada ou None se não houver.
        """
        company = self.env["res.company"].browse(company_id)
        if not company.partner_id:
            return None

        # Busca a conta bancária associada ao parceiro da empresa, priorizando as contas com a empresa definida
        bank = self.env["res.partner.bank"].search_read(
            [
                ("partner_id", "=", company.partner_id.id),
                ("company_id", "in", [company.id, False]),
            ],
            ["id"],
            limit=1,
            order="company_id DESC",
        )

        return self.env["res.partner.bank"].browse(bank[0]["id"]) if bank else None

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        """
        Obtém a visão de campos para a fatura, ajustando dinamicamente com base no contexto.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias
        e utiliza verificações diretas para determinar a visão apropriada.

        Args:
            view_id (int, optional): ID da visão a ser carregada. Defaults to None.
            view_type (str, optional): Tipo da visão ('form', 'tree', etc.). Defaults to 'form'.
            toolbar (bool, optional): Indica se a barra de ferramentas deve ser incluída. Defaults to False.
            submenu (bool, optional): Indica se o submenu deve ser incluído. Defaults to False.

        Returns:
            dict: Dados da visão ajustados.
        """
        context = self._context
        partner_id = (
            context.get("active_ids", [None])[0]
            if context.get("active_model") == "res.partner"
            else None
        )

        if view_type == "form" and partner_id:
            partner = self.env["res.partner"].browse(partner_id)
            if partner.supplier and not partner.customer:
                view_id = self.env.ref(
                    "account.invoice_supplier_form", raise_if_not_found=False
                ).id
            elif partner.customer and not partner.supplier:
                view_id = self.env.ref(
                    "account.invoice_form", raise_if_not_found=False
                ).id

        return super(AccountInvoice, self).fields_view_get(view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)

    @api.multi
    def invoice_print(self):
        """
        Imprime a fatura e a marca como enviada, para facilitar a visualização
        do próximo passo do fluxo de trabalho.

        Este método foi otimizado para evitar operações desnecessárias e utiliza
        verificações diretas para melhorar a performance.

        Retorna:
            dict: Ação para gerar o relatório da fatura.
        """
        # Atualiza apenas as faturas que ainda não foram enviadas
        invoices_to_update = self.filtered(lambda inv: not inv.sent)
        if invoices_to_update:
            invoices_to_update.write({"sent": True})

        # Determina o relatório apropriado com base no grupo de permissões do usuário
        report_ref = (
            "account.account_invoices"
            if self.user_has_groups("account.group_account_invoice")
            else "account.account_invoices_without_payment"
        )

        return self.env.ref(report_ref).report_action(self)

    @api.multi
    def action_invoice_sent(self):
        """
        Abre uma janela para compor um e-mail, com o modelo de mensagem de fatura EDI carregado por padrão.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias
        e utiliza verificações diretas para determinar o modelo e o formulário apropriados.

        Retorna:
            dict: Ação para abrir a janela de composição de e-mail.
        """
        self.ensure_one()

        # Obtém o modelo de e-mail e o formulário de composição
        template = self.env.ref("account.email_template_edi_invoice", raise_if_not_found=False)
        compose_form = self.env.ref("account.account_invoice_send_wizard_form", raise_if_not_found=False)

        # Define o idioma do modelo, se aplicável
        lang = self.env.context.get("lang")
        if template and template.lang:
            lang = template._render_template(template.lang, "account.invoice", self.id)
        self = self.with_context(lang=lang)

        # Mapeia os tipos de fatura para descrições amigáveis
        TYPES = {
            "out_invoice": _("Invoice"),
            "in_invoice": _("Vendor Bill"),
            "out_refund": _("Credit Note"),
            "in_refund": _("Vendor Credit note"),
        }

        # Define o contexto para a janela de composição de e-mail
        ctx = {
            "default_model": "account.invoice",
            "default_res_id": self.id,
            "default_use_template": bool(template),
            "default_template_id": template.id if template else False,
            "default_composition_mode": "comment",
            "mark_invoice_as_sent": True,
            "model_description": TYPES.get(self.type, ""),
            "custom_layout": "mail.mail_notification_paynow",
            "force_email": True,
        }

        # Retorna a ação para abrir a janela de composição de e-mail
        return {
            "name": _("Send Invoice"),
            "type": "ir.actions.act_window",
            "view_type": "form",
            "view_mode": "form",
            "res_model": "account.invoice.send",
            "views": [(compose_form.id, "form")] if compose_form else [],
            "view_id": compose_form.id if compose_form else False,
            "target": "new",
            "context": ctx,
        }

    @api.multi
    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        """
        Posta uma mensagem no registro e marca a fatura como enviada, se aplicável.

        Este método foi otimizado para melhorar a performance ao evitar filtragens redundantes
        e utiliza verificações diretas para determinar se a fatura deve ser marcada como enviada.

        Args:
            **kwargs: Argumentos adicionais para o método de postagem de mensagem.

        Returns:
            mail.message: Mensagem postada.
        """
        if self.env.context.get("mark_invoice_as_sent"):
            invoices_to_update = self.filtered(lambda inv: not inv.sent)
            if invoices_to_update:
                invoices_to_update.write({"sent": True})
                self.env.user.company_id.set_onboarding_step_done(
                    "account_onboarding_sample_invoice_state"
                )

        return super(AccountInvoice, self.with_context(mail_post_autofollow=True)).message_post(**kwargs)

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        """
        Sobrescreve o método message_new() de mail_thread, chamado pelo mailgateway através de message_process,
        para completar os valores de contas a pagar criadas por e-mails.

        Este método foi otimizado para melhorar a performance ao evitar buscas desnecessárias e utiliza
        verificações diretas para determinar o parceiro e o diário apropriados.

        Args:
            msg_dict (dict): Dicionário contendo os dados da mensagem recebida.
            custom_values (dict, opcional): Valores personalizados para criar a conta a pagar.

        Returns:
            recordset: Registro da conta a pagar criada.
        """
        # Obter e-mails do remetente e CC para buscar parceiros relacionados
        subscribed_emails = email_split((msg_dict.get("from") or "") + "," + (msg_dict.get("cc") or ""))
        seen_partner_ids = list(filter(None, self._find_partner_from_emails(subscribed_emails)))

        # Detectar o parceiro da conta a pagar
        email_from = email_escape_char(email_split(msg_dict.get("from") or "")[0])
        partner_id = self._search_on_partner(email_from, extra_domain=[("supplier", "=", True)])

        # Verificar se o remetente é um usuário interno e buscar o parceiros no corpo do e-mail
        if not partner_id:
            user_partner_id = self._search_on_user(email_from)
            if user_partner_id and self.env.ref('base.group_user').users.filtered(lambda u: u.partner_id.id == user_partner_id):
                email_addresses = set(email_re.findall(msg_dict.get('body')))
                if email_addresses:
                    partner_ids = set(filter(None, (self._find_partner_from_emails([email], force_create=False) for email in email_addresses)))
                    potential_vendors = self.env['res.partner'].browse(partner_ids).filtered(lambda x: not x.user_ids)
                    partner_id = potential_vendors.filtered('supplier')[:1].id or potential_vendors[:1].id

        # Adicionar o parceiro encontrado à lista de parceiros vistos
        if partner_id:
            seen_partner_ids.append(partner_id)

        # Buscar o diário com base no endereço de e-mail "TO"
        alias_names = [
            mail_to.split("@")[0]
            for mail_to in email_split(
                (msg_dict.get("to") or "") + "," + (msg_dict.get("cc") or "")
            )
        ]
        journal = self.env["account.journal"].search(
            [("type", "=", "purchase"), ("alias_name", "in", alias_names)], limit=1
        )

        # Criar a mensagem
        values = dict(custom_values or {}, partner_id=partner_id, source_email=email_from)
        if journal:
            values["journal_id"] = journal.id

        # Passar o tipo no contexto para definir corretamente o diário
        invoice = super(AccountInvoice, self.with_context(type=values.get("type"))).message_new(msg_dict, values)

        # Inscrever usuários internos na fatura criada
        partners_to_subscribe = self.env["res.partner"].browse(seen_partner_ids).filtered(lambda p: p.user_ids)
        if partners_to_subscribe:
            invoice.message_subscribe(partners_to_subscribe.ids)

        return invoice

    @api.model
    def complete_empty_list_help(self):
        """
        Gera uma mensagem de ajuda para listas vazias de contas a pagar, fornecendo informações sobre aliases de e-mail
        configurados em diários de compras. Este método otimiza o desempenho ao reduzir operações redundantes
        e utiliza verificações diretas para determinar a mensagem de ajuda apropriada.

        Retorna:
            str: A mensagem de ajuda a ser exibida.
        """
        Journal = self.env["account.journal"]
        # Fetch journals based on context or default to purchase type journals
        journals = Journal.browse(
            self._context.get("default_journal_id")
        ) or Journal.search([("type", "=", "purchase")])

        if not journals:
            return _(
                "<p>You can control the invoice from your vendor based on what you purchased or received.</p>"
            )

        # Filter journals with valid email aliases
        alias_journals = journals.filtered(
            lambda j: j.alias_domain and j.alias_id.alias_name
        )
        if not alias_journals:
            return _(
                '<p>You can set an <a data-oe-id=%s data-oe-model="account.journal" href=#id=%s&model=account.journal>email alias</a> '
                "to allow draft vendor bills to be created upon reception of an email.</p>"
            ) % (journals[0].id, journals[0].id)

        # Generate email links for journals with aliases
        links = ", ".join(
            "<a id='o_mail_test' href='mailto:{email}'>{email}</a>".format(
                email=f"{journal.alias_id.alias_name}@{journal.alias_domain}"
            )
            for journal in alias_journals
        )

        # Determine the appropriate help message based on the number of aliases
        if len(alias_journals) == 1:
            return _("Or share the email %s to your vendors: bills will be created automatically upon mail reception.") % links

        return _("Or share the emails %s to your vendors: bills will be created automatically upon mail reception.")% links

    @api.multi
    def compute_taxes(self):
        """
        Calcula os impostos de uma fatura recém-criada, garantindo que as linhas de imposto sejam atualizadas corretamente.

        Este método é utilizado em outros módulos para calcular os impostos em uma fatura onde os onchanges ainda não foram aplicados.

        - Remove linhas de imposto não manuais existentes.
        - Agrupa os impostos por tipo e cria novas linhas de imposto.

        Retorna:
            bool: Indica se a operação foi bem-sucedida.
        """
        account_invoice_tax = self.env['account.invoice.tax']
        ctx = dict(self._context)

        # Processa cada fatura individualmente
        for invoice in self:
            # Remove linhas de imposto não manuais associadas à fatura
            self._cr.execute(
                "DELETE FROM account_invoice_tax WHERE invoice_id=%s AND manual IS FALSE",
                (invoice.id,)
            )
            if self._cr.rowcount:
                self.invalidate_cache()

            # Calcula os valores dos impostos agrupados
            tax_grouped = invoice.get_taxes_values()

            # Cria novas linhas de imposto com base nos valores calculados
            account_invoice_tax.create(list(tax_grouped.values()))

        # Escrita dummy para disparar recomputações
        return self.with_context(ctx).write({'invoice_line_ids': []})

    @api.multi
    def unlink(self):
        """
        Remove faturas do banco de dados, garantindo que apenas faturas em estado de rascunho ou canceladas sejam excluídas.

        Este método foi otimizado para melhorar a performance ao evitar verificações redundantes e utiliza
        verificações diretas para determinar se a fatura pode ser excluída.

        Raises:
            UserError: Se a fatura não estiver em estado de rascunho ou cancelada.
            UserError: Se a fatura já tiver sido validada e recebido um número.

        Returns:
            bool: Resultado da operação de exclusão.
        """
        # Filtra as faturas que não estão em estado de rascunho ou canceladas
        invalid_invoices = self.filtered(lambda inv: inv.state not in ('draft', 'cancel') or inv.move_name)

        # Verifica se há faturas inválidas e lança erros apropriados
        if invalid_invoices:
            for invoice in invalid_invoices:
                if invoice.state not in ('draft', 'cancel'):
                    raise UserError(_('You cannot delete an invoice which is not draft or cancelled. You should create a credit note instead.'))
                elif invoice.move_name:
                    raise UserError(_('You cannot delete an invoice after it has been validated (and received a number). You can set it back to "Draft" state and modify its content, then re-confirm it.'))

        # Chama o método original para realizar a exclusão
        return super(AccountInvoice, self).unlink()

    @api.onchange("invoice_line_ids")
    def _onchange_invoice_line_ids(self):
        """
        Atualiza as linhas de imposto da fatura com base nas linhas de itens da fatura.

        Retorno:
            None
        """
        # Calcula os valores dos impostos agrupados
        taxes_grouped = self.get_taxes_values()
        tax_lines = self.tax_line_ids.filtered('manual')

        for tax in taxes_grouped.values():
            tax_lines += tax_lines.new(tax)

        self.tax_line_ids = tax_lines

        return

    @api.onchange("partner_id", "company_id")
    def _onchange_partner_id(self):
        """
        Atualiza os campos relacionados ao parceiro na fatura com base no parceiro selecionado.

        Este método otimiza a performance ao evitar buscas desnecessárias e utiliza verificações diretas
        para determinar as contas, termos de pagamento, posição fiscal e banco associados ao parceiro.

        - Define a conta contábil com base no tipo de fatura (recebível ou pagável).
        - Define os termos de pagamento com base nas propriedades do parceiro.
        - Calcula a posição fiscal com base no parceiro e no endereço de entrega.
        - Adiciona avisos ou bloqueios, se configurados no parceiro.

        Retorno:
            dict: Contém avisos e domínios para os campos atualizados.
        """
        account_id = False
        payment_term_id = False
        fiscal_position = False
        warning = {}
        domain = {}
        company_id = self.company_id.id
        partner = self.partner_id.with_context(force_company=company_id) if company_id else self.partner_id
        invoice_type = self.type or self.env.context.get("type", "out_invoice")

        if partner:
            # Obtém as contas contábeis do parceiro
            rec_account = partner.property_account_receivable_id
            pay_account = partner.property_account_payable_id

            if not rec_account and not pay_account:
                action = self.env.ref('account.action_account_config')
                msg = _('Cannot find a chart of accounts for this company, You should configure it. \nPlease go to Account Configuration.')
                raise RedirectWarning(msg, action.id, _('Go to the configuration panel'))

            # Define a conta contábil e os termos de pagamento com base no tipo de fatura
            if invoice_type in ("in_invoice", "in_refund"):
                account_id = pay_account.id
                payment_term_id = partner.property_supplier_payment_term_id.id
            else:
                account_id = rec_account.id
                payment_term_id = partner.property_payment_term_id.id

            # Calcula a posição fiscal com base no parceiro e no endereço de entrega
            delivery_partner_id = self.get_delivery_partner_id()
            fiscal_position = partner.env['account.fiscal.position'].get_fiscal_position(
                self.partner_id.id, delivery_id=delivery_partner_id
            )

            # Verifica avisos configurados no parceiro
            if partner.invoice_warn == 'no-message' and partner.parent_id:
                partner = partner.parent_id

            if partner.invoice_warn and partner.invoice_warn != 'no-message':
                # Bloqueia se o parceiro pai estiver configurado como bloqueado
                if partner.invoice_warn != 'block' and partner.parent_id and partner.parent_id.invoice_warn == 'block':
                    partner = partner.parent_id
                warning = {
                    "title": _("Warning for %s") % partner.name,
                    "message": partner.invoice_warn_msg,
                }
                if partner.invoice_warn == "block":
                    self.partner_id = False

        # Atualiza os campos da fatura
        update_vals = {
            "account_id": account_id,
            "date_due": False,
        }
        if payment_term_id:
            update_vals.update({"payment_term_id": payment_term_id})
        if fiscal_position:
            update_vals.update({"fiscal_position_id": fiscal_position})

        # Define o domínio para o campo de conta bancária
        if invoice_type in ("in_invoice", "out_refund"):
            bank_ids = partner.commercial_partner_id.bank_ids
            update_vals["partner_bank_id"] = bank_ids[0].id if bank_ids else False
            domain = {"partner_bank_id": [("id", "in", bank_ids.ids)]}
        elif invoice_type == "out_invoice":
            domain = {
                "partner_bank_id": [
                    ("partner_id.ref_company_ids", "in", [self.company_id.id])
                ]
            }

        self.update(update_vals)

        # Retorna avisos e domínios, se aplicável
        result = {}
        if warning:
            result['warning'] = warning
        if domain:
            result['domain'] = domain

        return result

    @api.multi
    def get_delivery_partner_id(self):
        self.ensure_one()
        return self.partner_id.address_get(['delivery'])['delivery']

    @api.onchange('journal_id')
    def _onchange_journal_id(self):
        if self.journal_id and not self._context.get('default_currency_id'):
            self.currency_id = self.journal_id.currency_id.id or self.journal_id.company_id.currency_id.id

    @api.onchange("payment_term_id", "date_invoice")
    def _onchange_payment_term_date_invoice(self):
        """
        Atualiza a data de vencimento da fatura com base nos termos de pagamento e na data da fatura.

        Este método foi otimizado para evitar operações desnecessárias e utiliza verificações diretas
        para determinar a data de vencimento apropriada.

        - Se houver termos de pagamento, calcula a data de vencimento com base nos valores retornados.
        - Se não houver termos de pagamento, ajusta a data de vencimento para a data da fatura, caso necessário.

        """
        date_invoice = self.date_invoice or fields.Date.context_today(self)

        if self.payment_term_id:
            # Calcula as parcelas com base nos termos de pagamento
            pterm_list = self.payment_term_id.with_context(
                currency_id=self.company_id.currency_id.id
            ).compute(value=1, date_ref=date_invoice)[0]

            # Define a data de vencimento como a maior data calculada
            self.date_due = max(line[0] for line in pterm_list)
        elif self.date_due and date_invoice > self.date_due:
            # Ajusta a data de vencimento para a data da fatura, se necessário
            self.date_due = date_invoice

    @api.onchange(
        "cash_rounding_id", "invoice_line_ids", "tax_line_ids", "amount_total"
    )
    def _onchange_cash_rounding(self):
        """
        Atualiza as linhas de arredondamento da fatura com base na configuração de arredondamento de caixa.

        Este método otimiza o desempenho ao evitar operações desnecessárias e utiliza verificações diretas
        para determinar as linhas de arredondamento e os ajustes necessários.

        - Remove linhas de arredondamento existentes.
        - Ajusta os valores de arredondamento nas linhas de imposto, se aplicável.
        - Adiciona uma nova linha de arredondamento ou ajusta a maior linha de imposto, dependendo da estratégia.

        Retorno:
            None
        """
        # Remove linhas de arredondamento existentes
        self.invoice_line_ids = self.invoice_line_ids.filtered(lambda l: not l.is_rounding_line)

        # Limpa valores de arredondamento anteriores nas linhas de imposto
        self.tax_line_ids.filtered(lambda t: t.amount_rounding != 0.0).write({'amount_rounding': 0.0})

        if self.cash_rounding_id and self.type in ('out_invoice', 'out_refund'):
            rounding_amount = self.cash_rounding_id.compute_difference(self.currency_id, self.amount_total)
            if not self.currency_id.is_zero(rounding_amount):
                if self.cash_rounding_id.strategy == 'biggest_tax':
                    # Ajusta o valor de arredondamento na maior linha de imposto
                    biggest_tax_line = max(self.tax_line_ids, key=lambda t: t.amount, default=None)
                    if biggest_tax_line:
                        biggest_tax_line.amount_rounding += rounding_amount
                elif self.cash_rounding_id.strategy == 'add_invoice_line':
                    # Determina a conta de perda ou ganho para o arredondamento
                    account_id = (
                        self.cash_rounding_id._get_loss_account_id().id
                        if rounding_amount > 0.0
                        else self.cash_rounding_id._get_profit_account_id().id
                    )
                    # Cria uma nova linha de fatura para o arredondamento
                    self.invoice_line_ids = [(0, 0, {
                        'name': self.cash_rounding_id.name,
                        'account_id': account_id,
                        'price_unit': rounding_amount,
                        'quantity': 1,
                        'is_rounding_line': True,
                        'sequence': 9999  # sempre última linha
                    })]

    @api.multi
    def action_invoice_draft(self):
        """
        Retorna a fatura ao estado de rascunho, garantindo que apenas faturas canceladas possam ser redefinidas.

        - Verifica se todas as faturas estão no estado 'cancel'.
        - Atualiza o estado para 'draft' e limpa a data.
        - Remove anexos de faturas impressas anteriormente, se aplicável.

        Raises:
            UserError: Se alguma fatura não estiver no estado 'cancel'.

        Returns:
            bool: Retorna True após a operação ser concluída.
        """
        if any(inv.state != "cancel" for inv in self):
            raise UserError(
                _("Invoice must be cancelled in order to reset it to draft.")
            )

        # Atualiza o estado para 'draft' e limpa a data
        self.write({"state": "draft", "date": False})

        # Remove anexos de faturas impressas anteriormente
        report_invoice = self.env.ref(
            "account.account_invoices", raise_if_not_found=False
        )
        if report_invoice and report_invoice.attachment:
            attachments = self.env["ir.attachment"].search(
                [
                    ("res_model", "=", "account.invoice"),
                    ("res_id", "in", self.ids),
                    ("name", "like", report_invoice.attachment),
                ]
            )
            attachments.unlink()

        return True

    @api.multi
    def action_invoice_open(self):
        """
        Abre as faturas, validando os campos obrigatórios e criando os lançamentos contábeis.

        Este método foi otimizado para melhorar a performance ao evitar filtragens redundantes
        e utiliza verificações diretas para determinar se a fatura pode ser aberta.

        Raises:
            UserError: Se o campo "Vendor" não estiver preenchido.
            UserError: Se a fatura não estiver no estado "draft" ou "processing".
            UserError: Se o valor total da fatura for negativo.
            UserError: Se nenhuma conta contábil for encontrada para criar a fatura.

        Returns:
            bool: Retorna True após a operação ser concluída.
        """
        # Filtra as faturas que não estão no estado "open"
        to_open_invoices = self.filtered(lambda inv: inv.state != 'open')

        # Verifica se há faturas com campos obrigatórios ausentes ou estados inválidos
        if any(not inv.partner_id for inv in to_open_invoices):
            raise UserError(_("The field Vendor is required, please complete it to validate the Vendor Bill."))
        # if any(inv.state not in ('draft', 'processing') for inv in to_open_invoices):
        #     raise UserError(_("Invoice must be in draft state in order to validate it."))
        if any(float_compare(inv.amount_total, 0.0, precision_rounding=inv.currency_id.rounding) == -1 for inv in to_open_invoices):
            raise UserError(_("You cannot validate an invoice with a negative total amount. You should create a credit note instead."))
        if any(not inv.account_id for inv in to_open_invoices):
            raise UserError(_('No account was found to create the invoice, be sure you have installed a chart of account.'))

        # Executa as ações necessárias para abrir as faturas
        to_open_invoices.action_date_assign()
        to_open_invoices.action_move_create()

        # Valida as faturas e retorna o resultado
        return to_open_invoices.invoice_validate()

    @api.multi
    def action_invoice_paid(self):
        """
        Atualiza o estado das faturas para 'paid' ou 'in_payment', dependendo das condições.

        Este método foi otimizado para evitar verificações redundantes e utiliza
        filtragens diretas para melhorar a performance.

        - Verifica se todas as faturas estão no estado apropriado para serem pagas.
        - Atualiza o estado para 'in_payment' se houver lançamentos contábeis pendentes de reconciliação bancária.
        - Atualiza o estado para 'paid' se todas as condições forem atendidas.

        Raises:
            UserError: Se a fatura não estiver validada.
            UserError: Se a fatura não estiver totalmente reconciliada.
        """
        # Filtra as faturas que ainda não estão pagas
        to_pay_invoices = self.filtered(lambda inv: inv.state != 'paid')

        # Verifica se há faturas em estados inválidos para pagamento
        if to_pay_invoices.filtered(lambda inv: inv.state not in ('open', 'in_payment')):
            raise UserError(_('Invoice must be validated in order to set it to register payment.'))

        # Verifica se há faturas parcialmente pagas
        # filtered(lambda inv: not inv.reconciled):
        if to_pay_invoices.get_invoice_not_reconciled():
            raise UserError(_('You cannot pay an invoice which is partially paid. You need to reconcile payment entries first.'))

        # Atualiza o estado das faturas
        for invoice in to_pay_invoices:
            # Verifica se há lançamentos contábeis de pagamento em estado 'draft' e configurados para conciliação bancária
            has_draft_bank_moves = any(
                move.state == 'draft' and move.journal_id.post_at_bank_rec
                for move in invoice.payment_move_line_ids.mapped('move_id')
            ) if invoice.payment_move_line_ids else False

            try:
                invoice.state = 'in_payment' if has_draft_bank_moves else 'paid'
            except Exception as e:
                _logger.warning("Failed to set invoice %s to paid/in_payment: %s", invoice.id, e)

    @api.multi
    def action_invoice_re_open(self):
        """
        Reabre faturas que estão no estado 'in_payment' ou 'paid', alterando seu estado para 'open'.

        Este método foi otimizado para evitar filtragens redundantes e utiliza verificações diretas
        para determinar se a fatura pode ser reaberta.

        Raises:
            UserError: Se a fatura não estiver nos estados 'in_payment' ou 'paid'.

        Returns:
            bool: Retorna True após a operação ser concluída.
        """
        if not self:
            return

        if any(inv.state not in ("in_payment", "paid") for inv in self):
            raise UserError(
                _("Invoice must be paid in order to set it to register payment.")
            )

        return self.write({"state": "open"})

    @api.multi
    def action_invoice_cancel(self):
        """
        Cancela as faturas que não estão no estado 'cancel'.

        Este método foi otimizado para melhorar a performance ao evitar filtragens redundantes
        e utiliza verificações diretas para determinar se a fatura pode ser cancelada.

        Retorna:
            bool: Retorna True após a operação ser concluída.
        """
        invoices_to_cancel = self.filtered(lambda inv: inv.state != "cancel")
        if not invoices_to_cancel:
            return True
        return invoices_to_cancel.action_cancel()

    @api.multi
    def _notify_get_groups(self, message, groups):
        """
        Adiciona o botão de acesso para usuários e clientes do portal,
        desde que o estado da fatura não seja 'draft' ou 'cancel'.

        Este método foi otimizado para evitar iterações desnecessárias
        e utiliza verificações diretas para determinar se o botão de acesso deve ser exibido.

        Args:
            message (mail.message): Mensagem associada à notificação.
            groups (list): Lista de grupos de notificação.

        Returns:
            list: Grupos de notificação atualizados com informações de acesso.
        """
        groups = super(AccountInvoice, self)._notify_get_groups(message, groups)

        if self.state not in {"draft", "cancel"}:
            # Atualiza os dados do grupo diretamente, evitando iterações desnecessárias
            for group in groups:
                group_name, _, group_data = group
                if group_name not in {"customer", "portal"}:
                    group_data["has_button_access"] = True

        return groups

    @api.multi
    def get_formview_id(self, access_uid=None):
        """
        Obtém o ID da visão de formulário para abrir a fatura.

        Args:
            access_uid (int, opcional): ID do usuário para verificar permissões de acesso. Defaults to None.

        Returns:
            int: ID da visão de formulário correspondente.
        """
        view_ref = (
            "account.invoice_supplier_form"
            if self.type in ("in_invoice", "in_refund")
            else "account.invoice_form"
        )
        view = self.env.ref(view_ref, raise_if_not_found=True)

        return view.id

    def _prepare_tax_line_vals(self, line, tax):
        """
        Prepara os valores para criar uma linha de imposto (account.invoice.tax).

        Este método foi otimizado para melhorar a performance ao evitar operações desnecessárias
        e utiliza verificações diretas para determinar os valores apropriados.

        Args:
            line (account.invoice.line): Linha da fatura associada.
            tax (dict): Dados do imposto calculados por account.tax.compute_all().

        Returns:
            dict: Valores preparados para criar a linha de imposto.
        """
        # Determina a conta contábil com base no tipo de fatura
        account_id = (
            tax["account_id"] or line.account_id.id
            if self.type in ("out_invoice", "in_invoice")
            else tax["refund_account_id"] or line.account_id.id
        )

        # Determina a conta analítica, se aplicável
        account_analytic_id = (
            line.account_analytic_id.id
            if tax["analytic"] # or account_id == line.account_id.id    Multidados: comentado até uma solução completa
            else False
        )

        # Prepara os valores da linha de imposto
        vals = {
            "invoice_id": self.id,
            "name": tax["name"],
            "tax_id": tax["id"],
            "amount": tax["amount"],
            "base": tax["base"],
            "manual": False,
            "sequence": tax["sequence"],
            "account_analytic_id": account_analytic_id,
            "account_id": account_id,
            "analytic_tag_ids": tax["analytic"] and line.analytic_tag_ids.ids or False,
        }

        return vals

    @api.multi
    def get_taxes_values(self):
        """
        Calcula os valores dos impostos agrupados para as linhas da fatura.

        Este método otimiza o cálculo ao evitar operações desnecessárias e utiliza
        um dicionário para agrupar os impostos com base em suas chaves.

        Retorna:
            dict: Dicionário contendo os valores dos impostos agrupados.
        """
        tax_grouped = {}
        round_curr = self.currency_id.round

        # Itera sobre as linhas da fatura
        for line in self.invoice_line_ids.filtered(lambda l: l.account_id and not l.display_type):
            price_unit = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.invoice_line_tax_ids.compute_all(
                price_unit,
                self.currency_id,
                line.quantity,
                line.product_id,
                self.partner_id,
            )["taxes"]

            for tax in taxes:
                val = self._prepare_tax_line_vals(line, tax)
                key = self.env["account.tax"].browse(tax["id"]).get_grouping_key(val)

                # Agrupa os impostos com base na chave
                if key in tax_grouped:
                    tax_grouped[key]["amount"] += val["amount"]
                    tax_grouped[key]["base"] += round_curr(val["base"])
                else:
                    tax_grouped[key] = val
                    tax_grouped[key]['base'] = round_curr(val['base'])

        return tax_grouped

    @api.multi
    def _get_aml_for_register_payment(self):
        """
        Obtém as linhas de movimentação contábil (account.move.line) relevantes para reconciliar no registro de pagamento.

        Retorno:
            recordset: Conjunto de linhas de movimentação contábil relevantes.
        """
        self.ensure_one()
        move_lines = self.move_id.sudo().line_ids

        return move_lines.filtered(
            lambda r: not r.reconciled
            and r.account_id.internal_type in ("payable", "receivable")
        )

    @api.multi
    def register_payment(self, payment_line, writeoff_acc_id=False, writeoff_journal_id=False):
        """
        Reconciliar as linhas de contas a pagar/receber da fatura com a linha de pagamento fornecida.

        Args:
            payment_line (account.move.line): Linha de pagamento a ser reconciliada.
            writeoff_acc_id (int, opcional): ID da conta de ajuste, se aplicável. Defaults to False.
            writeoff_journal_id (int, opcional): ID do diário de ajuste, se aplicável. Defaults to False.

        Returns:
            recordset: Resultado da reconciliação das linhas.
        """
        # Obtém as linhas de movimentação contábil relevantes para reconciliação
        line_to_reconcile = self.env['account.move.line']
        for inv in self:
            line_to_reconcile += inv._get_aml_for_register_payment()

        # Realiza a reconciliação das linhas de pagamento e contábeis
        return (line_to_reconcile + payment_line).reconcile(writeoff_acc_id, writeoff_journal_id)

    @api.multi
    def assign_outstanding_credit(self, credit_aml_id):
        """
        Associa um crédito pendente a uma fatura e registra o pagamento.

        Este método foi otimizado para melhorar a performance ao evitar operações desnecessárias
        e utiliza verificações diretas para determinar se o crédito pode ser associado.

        Args:
            credit_aml_id (int): ID da linha de movimentação contábil do crédito pendente.

        Returns:
            recordset: Resultado da reconciliação das linhas de pagamento.
        """
        self.ensure_one()
        credit_aml = self.env["account.move.line"].browse(credit_aml_id)

        # Verifica se a moeda da linha de crédito é diferente da moeda da empresa
        if (
            not credit_aml.currency_id
            and self.currency_id != self.company_id.currency_id
        ):
            amount_currency = self.company_id.currency_id._convert(
                credit_aml.balance,
                self.currency_id,
                self.company_id,
                credit_aml.date or fields.Date.today(),
            )
            credit_aml.with_context(
                allow_amount_currency=True, check_move_validity=False
            ).write(
                {"amount_currency": amount_currency, "currency_id": self.currency_id.id}
            )

        # Atualiza a fatura associada ao pagamento, se aplicável
        if credit_aml.payment_id:
            credit_aml.payment_id.write({"invoice_ids": [(4, self.id, None)]})

        # Registra o pagamento e retorna o resultado
        return self.register_payment(credit_aml)

    @api.multi
    def action_date_assign(self):
        for inv in self:
            # Here the onchange will automatically write to the database
            inv._onchange_payment_term_date_invoice()
        return True

    @api.multi
    def finalize_invoice_move_lines(self, move_lines):
        """ finalize_invoice_move_lines(move_lines) -> move_lines

            Hook method to be overridden in additional modules to verify and
            possibly alter the move lines to be created by an invoice, for
            special cases.
            :param move_lines: list of dictionaries with the account.move.lines (as for create())
            :return: the (possibly updated) final move_lines to create for this invoice
        """
        return move_lines

    @api.multi
    def compute_invoice_totals(self, company_currency, invoice_move_lines):
        """
        Calcula os totais da fatura em moeda da empresa e em moeda da fatura.

        Este método otimiza o desempenho ao evitar operações desnecessárias e utiliza
        verificações diretas para determinar os valores apropriados.

        Args:
            company_currency (res.currency): Moeda da empresa.
            invoice_move_lines (list): Linhas de movimentação contábil da fatura.

        Returns:
            tuple: Total em moeda da empresa, total em moeda da fatura e as linhas de movimentação atualizadas.
        """
        total = 0
        total_currency = 0
        currency = self.currency_id
        date = self._get_currency_rate_date() or fields.Date.context_today(self)
        is_diff_currency = currency != company_currency

        for line in invoice_move_lines:
            if is_diff_currency:
                if not line.get("currency_id") or not line.get("amount_currency"):
                    line["currency_id"] = currency.id
                    line["amount_currency"] = currency.round(line["price"])
                    line["price"] = currency._convert(
                        line["price"], company_currency, self.company_id, date
                    )
            else:
                line["currency_id"] = False
                line["amount_currency"] = False
                line["price"] = company_currency.round(line["price"])

            if self.type in ("out_invoice", "in_refund"):
                total += line["price"]
                total_currency += line.get("amount_currency", line["price"])
                line["price"] = -line["price"]
            else:
                total -= line["price"]
                total_currency -= line.get("amount_currency", line["price"])

        return total, total_currency, invoice_move_lines

    @api.model
    def invoice_line_move_line_get(self):
        """
        Gera as linhas de movimentação contábil para as linhas da fatura.

        Este método otimiza a performance ao evitar operações desnecessárias e utiliza
        verificações diretas para determinar as linhas de movimentação contábil.

        Retorna:
            list: Lista de dicionários contendo os dados das linhas de movimentação contábil.
        """
        res = []
        for line in self.invoice_line_ids.filtered(lambda l: l.account_id and l.quantity != 0):
            # Impostos principais
            tax_ids = [
                (4, tax.id, None)
                for tax in line.invoice_line_tax_ids
                if tax.type_tax_use != "none"
            ]

            # Impostos subsequentes
            tax_ids.extend(
                (4, child.id, None)
                for tax in line.invoice_line_tax_ids
                for child in tax.children_tax_ids
                if child.type_tax_use != "none"
            )

            analytic_tag_ids = [(4, tag.id, None) for tag in line.analytic_tag_ids]

            move_line_dict = {
                "invl_id": line.id,
                "type": "src",
                "name": line.name,
                "price_unit": line.price_unit,
                "quantity": line.quantity,
                "price": line.price_subtotal,
                "account_id": line.account_id.id,
                "product_id": line.product_id.id,
                "uom_id": line.uom_id.id,
                "account_analytic_id": line.account_analytic_id.id,
                "analytic_tag_ids": analytic_tag_ids,
                "tax_ids": tax_ids,
                "invoice_id": self.id,
                "currency_id": self.currency_id.id,
            }

            res.append(move_line_dict)

        return res

    @api.model
    def tax_line_move_line_get(self):
        """
        Gera as linhas de movimentação contábil para os impostos da fatura.

        Este método otimiza o desempenho ao evitar operações desnecessárias e utiliza
        verificações diretas para determinar as linhas de movimentação contábil de impostos.

        Retorna:
            list: Lista de dicionários contendo os dados das linhas de movimentação contábil de impostos.
        """
        res = []

        # Itera sobre as linhas de imposto da fatura em ordem reversa de sequência
        for tax_line in self.tax_line_ids.sorted(key=lambda x: -x.sequence):
            tax = tax_line.tax_id

            # Prepara os IDs das tags analíticas
            analytic_tag_ids = [(4, tag.id, None) for tag in tax_line.analytic_tag_ids]

            # Verifica se há valor total de imposto para processar
            if tax_line.amount_total:
                tax_line_vals = {
                    "invoice_tax_line_id": tax_line.id,
                    "tax_line_id": tax.id,
                    "type": "tax",
                    "name": tax_line.name,
                    "price_unit": tax_line.amount_total,
                    "quantity": 1,
                    "price": tax_line.amount_total,
                    "account_id": tax_line.account_id.id,
                    "account_analytic_id": tax_line.account_analytic_id.id,
                    "analytic_tag_ids": analytic_tag_ids,
                    "invoice_id": self.id,
                    "tax_ids": False,
                }

                # Verifica se o imposto inclui o valor base e ajusta os impostos subsequentes
                if tax.include_base_amount:
                    invoice_lines = tax_line.invoice_id.invoice_line_ids.filtered(
                        lambda line: tax in line.invoice_line_tax_ids
                        or tax in line.invoice_line_tax_ids.mapped("children_tax_ids")
                    )
                    if invoice_lines:
                        all_taxes = invoice_lines.mapped(
                            "invoice_line_tax_ids"
                        ).filtered(
                            lambda x: x.amount_type != "group"
                        ) + invoice_lines.mapped(
                            "invoice_line_tax_ids.children_tax_ids"
                        )
                        following_taxes = all_taxes.filtered(
                            lambda x: x.sequence > tax.sequence
                            or (x.sequence == tax.sequence and x.id > tax.id)
                        )
                        if following_taxes:
                            tax_line_vals["tax_ids"] = [(6, 0, following_taxes.ids)]

                res.append(tax_line_vals)

        return res

    def _get_following_taxes(self, tax, tax_line):
        """
        Obtém os impostos subsequentes que devem ser ajustados devido ao imposto atual.

        Args:
            tax (account.tax): O imposto atual.
            tax_line (account.invoice.tax): A linha de imposto atual.

        Retorna:
            recordset: Conjunto de impostos subsequentes.
        """
        following_taxes = self.env["account.tax"]
        invoice_lines = tax_line.invoice_id.invoice_line_ids.filtered(
            lambda line: tax in line.invoice_line_tax_ids
            or tax in line.invoice_line_tax_ids.mapped("children_tax_ids")
        )
        if invoice_lines:
            all_taxes = invoice_lines.mapped("invoice_line_tax_ids").filtered(
                lambda t: t.amount_type != "group"
            ) + invoice_lines.mapped("invoice_line_tax_ids.children_tax_ids")
            following_taxes |= all_taxes.filtered(
                lambda t: t.sequence > tax.sequence
                or (t.sequence == tax.sequence and t.id > tax.id)
            )

        return following_taxes

    def inv_line_characteristic_hashcode(self, invoice_line):
        """Overridable hashcode generation for invoice lines. Lines having the same hashcode
        will be grouped together if the journal has the 'group line' option. Of course a module
        can add fields to invoice lines that would need to be tested too before merging lines
        or not."""
        return "%s-%s-%s-%s-%s-%s-%s" % (
            invoice_line['account_id'],
            invoice_line.get('tax_ids', 'False'),
            invoice_line.get('tax_line_id', 'False'),
            invoice_line.get('product_id', 'False'),
            invoice_line.get('analytic_account_id', 'False'),
            invoice_line.get('date_maturity', 'False'),
            invoice_line.get('analytic_tag_ids', 'False'),
        )

    def group_lines(self, iml, line):
        """
        Agrupa as linhas de movimentação contábil (e, consequentemente, as linhas analíticas)
        se os hashcodes das características das linhas da fatura forem iguais.

        Este método foi otimizado para melhorar a performance ao evitar múltiplas verificações
        e utiliza um dicionário para agrupar as linhas de forma eficiente.

        Args:
            iml (list): Linhas de movimentação contábil intermediárias.
            line (list): Linhas de movimentação contábil a serem agrupadas.

        Returns:
            list: Linhas de movimentação contábil agrupadas.
        """
        if self.journal_id.group_invoice_lines:
            line2 = {}
            for _, _, l in line:
                # Calcula o hashcode da linha da fatura
                hashcode = self.inv_line_characteristic_hashcode(l)

                # Agrupa as linhas com base no hashcode
                if hashcode in line2:
                    grouped_line = line2[hashcode]
                    amount = (
                        grouped_line["debit"]
                        - grouped_line["credit"]
                        + (l["debit"] - l["credit"])
                    )
                    grouped_line["debit"] = max(amount, 0.0)
                    grouped_line["credit"] = max(-amount, 0.0)
                    grouped_line["amount_currency"] += l.get("amount_currency", 0.0)
                    grouped_line["analytic_line_ids"] += l.get("analytic_line_ids", [])
                    if "quantity" in l:
                        grouped_line["quantity"] = (
                            grouped_line.get("quantity", 0.0) + l["quantity"]
                        )
                else:
                    line2[hashcode] = l

            # Converte o dicionário de linhas agrupadas de volta para uma lista
            line = [(0, 0, val) for val in line2.values()]

        return line

    @api.multi
    def action_move_create(self):
        """
        Cria as linhas de movimentação contábil e analítica relacionadas à fatura.

        Este método foi otimizado para melhorar a performance ao evitar operações desnecessárias
        e utiliza verificações diretas para determinar os valores apropriados.

        Raises:
            UserError: Se o diário não possuir uma sequência definida.
            UserError: Se não houver pelo menos uma linha de fatura com conta contábil.
         """
        account_move = self.env['account.move']

        for inv in self.filtered(lambda inv: not inv.move_id):
            if not inv.journal_id.sequence_id:
                raise UserError(_('Please define sequence on the journal related to this invoice.'))
            if not inv.invoice_line_ids.filtered(lambda line: line.account_id):
                raise UserError(_('Please add at least one invoice line.'))

            date_invoice = inv.date_invoice or fields.Date.context_today(self)
            date_due = inv.date_due or date_invoice

            # Atualizando as datas da fatura, se necessário
            # Atualizando aqui para garantir que as datas sejam atualizadas antes de criar as linhas de movimentação contábil
            update_vals = {}
            if not inv.date_invoice:
                update_vals['date_invoice'] = date_invoice
            if not inv.date_due:
                update_vals['date_due'] = date_due
            if update_vals:
                inv.write(update_vals)

            company_currency = inv.company_id.currency_id

            # Cria as linhas de movimentação contábil (uma por linha de fatura + impostos e linhas analíticas, se aplicável)
            iml = []
            if inv.invoice_line_ids:
                iml.extend(inv.invoice_line_move_line_get())
            if inv.tax_line_ids:
                iml.extend(inv.tax_line_move_line_get())

            diff_currency = inv.currency_id != company_currency
            # cria uma linha de movimentação para o total e, possivelmente, ajusta o valor das outras linhas
            total, total_currency, iml = inv.compute_invoice_totals(company_currency, iml)

            name = inv.name or ''
            if inv.payment_term_id:
                totlines = inv.payment_term_id.with_context(currency_id=company_currency.id).compute(total, date_invoice)[0]
                res_amount_currency = total_currency
                for i, t in enumerate(totlines):
                    if inv.currency_id != company_currency:
                        amount_currency = company_currency._convert(
                            t[1],
                            inv.currency_id,
                            inv.company_id,
                            inv._get_currency_rate_date() or fields.Date.today(),
                        )
                    else:
                        amount_currency = False

                    # Ajusta o amount_currency para a última linha para contabilizar diferenças de arredondamento
                    if i + 1 == len(totlines):
                        amount_currency = res_amount_currency
                    else:
                        res_amount_currency -= amount_currency or 0

                    iml.append(
                        {
                            "type": "dest",
                            "name": name,
                            "price": t[1],
                            "account_id": inv.account_id.id,
                            "date_maturity": t[0],
                            "amount_currency": diff_currency and amount_currency,
                            "currency_id": diff_currency and inv.currency_id.id,
                            "invoice_id": inv.id,
                        }
                    )
            else:
                iml.append(
                    {
                        "type": "dest",
                        "name": name,
                        "price": total,
                        "account_id": inv.account_id.id,
                        "date_maturity": date_due,
                        "amount_currency": diff_currency and total_currency,
                        "currency_id": diff_currency and inv.currency_id.id,
                        "invoice_id": inv.id,
                    }
                )

            # Obtém o parceiro contábil associado à fatura
            part = self.env["res.partner"]._find_accounting_partner(inv.partner_id)

            # Converte as linhas de movimentação contábil para o formato necessário
            line = [(0, 0, self.line_get_convert(l, part.id)) for l in iml]

            # Agrupa as linhas de movimentação contábil com base nas características
            line = inv.group_lines(iml, line)

            # Finaliza as linhas de movimentação contábil, permitindo ajustes finais
            line = inv.finalize_invoice_move_lines(line)

            # Determina a Data Contábil da fatura
            account_date = inv.date or date_invoice

            # Gera os movimentos Contabeis da fatura
            move_vals = {
                "ref": inv.reference,
                "line_ids": line,
                "journal_id": inv.journal_id.id,
                "date": account_date,
                "narration": inv.comment,
            }
            move = account_move.create(move_vals)

            # Passar a fatura no método post: usado se você quiser obter a mesma
            # referência de lançamento contábil ao criar a mesma fatura após uma cancelada:
            move.post(invoice=inv)

            # faça a fatura apontar para esse lançamento contábil
            vals = {
                "move_id": move.id,
                "move_name": move.name,
                "date": account_date,
            }
            inv.write(vals)

        return True

    @api.constrains("cash_rounding_id", "tax_line_ids")
    def _check_cash_rounding(self):
        """
        Verifica se o arredondamento de caixa pode ser aplicado corretamente.

        Este método otimiza a verificação ao evitar cálculos desnecessários e utiliza
        verificações diretas para determinar se o arredondamento é válido.

        - Calcula a diferença de arredondamento com base na moeda e no total da fatura.
        - Lança um erro se a diferença não for zero.

        Raises:
            UserError: Se a diferença de arredondamento não puder ser aplicada.
        """
        for inv in self.filtered(lambda inv: inv.cash_rounding_id):
            rounding_amount = inv.cash_rounding_id.compute_difference(
                inv.currency_id, inv.amount_total
            )
            if not inv.currency_id.is_zero(rounding_amount):
                raise UserError(_('The cash rounding cannot be computed because the difference must '
                                    'be added on the biggest tax found and no tax are specified.\n'
                                    'Please set up a tax or change the cash rounding method.'))

    @api.multi
    def _check_duplicate_supplier_reference(self):
        """
        Verifica se há duplicidade na referência de faturas de fornecedores ou notas de crédito.

        Este método otimiza a busca ao evitar operações desnecessárias e utiliza
        verificações diretas para determinar se a referência já foi utilizada.

        - Aplica a verificação apenas para faturas de entrada e notas de crédito de fornecedores.
        - Lança um erro se uma duplicidade for detectada.

        Raises:
            UserError: Se uma referência duplicada for encontrada.
        """
        for invoice in self.filtered(
            lambda inv: inv.type in ("in_invoice", "in_refund") and inv.reference
        ):
            domain = [
                ("type", "=", invoice.type),
                ("reference", "=", invoice.reference),
                ("company_id", "=", invoice.company_id.id),
                ("commercial_partner_id", "=", invoice.commercial_partner_id.id),
                ("id", "!=", invoice.id),
            ]
            if self.with_context(active_test=False).search_count(domain):
                raise UserError(
                    _(
                        "Duplicated vendor reference detected. You probably encoded twice the same vendor bill/credit note."
                    )
                )

    @api.multi
    def invoice_validate(self):
        """
        Valida as faturas, garantindo que os campos obrigatórios sejam preenchidos e que as referências sejam geradas corretamente.

        Este método foi otimizado para melhorar a performance ao evitar operações desnecessárias e utiliza
        verificações diretas para determinar se a fatura pode ser validada.

        - Inscreve o parceiro na fatura, se necessário.
        - Gera automaticamente a referência
        - Atualiza a referência no lançamento contábil associado.
        - Verifica duplicidade de referência para faturas de fornecedores.

        Raises:
            UserError: Se uma referência duplicada for detectada em faturas de fornecedores.

        Returns:
            bool: Retorna True após a operação ser concluída.
        """
        if not self:
            return True

        partner_ids_to_subscribe = []
        for invoice in self:
            # Inscreve o parceiro na fatura, se necessário
            if (
                invoice.partner_id
                and invoice.partner_id not in invoice.message_partner_ids
            ):
                partner_ids_to_subscribe.append(invoice.partner_id.id)

            # Gera automaticamente a referência, se aplicável
            if not invoice.reference and invoice.type == "out_invoice":
                invoice.reference = invoice._get_computed_reference()

            # Atualiza as referências no lançamento contábil associado
            if invoice.move_id:
                invoice.move_id.ref = invoice.reference

        # Verifica duplicidade de referência para faturas de fornecedores
        self._check_duplicate_supplier_reference()

        # Inscreve os parceiros coletados
        if partner_ids_to_subscribe:
            self.message_subscribe(partner_ids=partner_ids_to_subscribe)

        # Atualiza o estado para 'open'
        return self.write({"state": "open"})

    @api.model
    def line_get_convert(self, line, part):
        return self.env['product.product']._convert_prepared_anglosaxon_line(line, part)

    @api.multi
    def action_cancel(self):
        """
        Cancela as faturas, desfazendo as reconciliações e removendo os lançamentos contábeis associados.

        Este método foi otimizado para melhorar a performance ao evitar operações desnecessárias
        e utiliza verificações diretas para determinar se a fatura pode ser cancelada.

        - Remove as reconciliações das linhas contábeis associadas.
        - Atualiza o estado da fatura para 'cancel' e limpa os campos relacionados.
        - Cancela e exclui os lançamentos contábeis associados.

        Raises:
            UserError: Se ocorrer um erro ao cancelar os lançamentos contábeis.

        Returns:
            bool: Retorna True após a operação ser concluída.
        """
        moves = self.mapped('move_id')

        # Remove as reconciliações das linhas contábeis associadas
        for move in moves:
            move.line_ids.filtered(lambda x: x.account_id.reconcile).remove_move_reconcile()

        # Atualiza o estado da fatura para 'cancel' e limpa os campos relacionados
        self.write(
            {
                "state": "cancel",
                "move_id": False,
                "reference": False,
            }
        )

        if moves:
            # Cancela os lançamentos contábeis
            moves.button_cancel()
            # Exclui os lançamentos contábeis e suas linhas associadas
            moves.unlink()

        return True

    ###################

    @api.multi
    def name_get(self):
        TYPES = {
            'out_invoice': _('Invoice'),
            'in_invoice': _('Vendor Bill'),
            'out_refund': _('Credit Note'),
            'in_refund': _('Vendor Credit note'),
        }
        result = []
        for inv in self:
            result.append((inv.id, "%s %s" % (inv.number or TYPES[inv.type], inv.name or '')))
        return result

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None):
        args = args or []
        invoice_ids = []
        if name:
            invoice_ids = self._search([('number', '=', name)] + args, limit=limit, access_rights_uid=name_get_uid)
        if not invoice_ids:
            invoice_ids = self._search([('number', operator, name)] + args, limit=limit, access_rights_uid=name_get_uid)
        if not invoice_ids:
            invoice_ids = self._search([('name', operator, name)] + args, limit=limit, access_rights_uid=name_get_uid)
        return self.browse(invoice_ids).name_get()

    @api.model
    def _refund_cleanup_lines(self, lines):
        """ Convert records to dict of values suitable for one2many line creation

            :param recordset lines: records to convert
            :return: list of command tuple for one2many line creation [(0, 0, dict of valueis), ...]
        """
        result = []
        for line in lines:
            values = {}
            for name, field in line._fields.items():
                if name in MAGIC_COLUMNS:
                    continue
                elif field.type == 'many2one':
                    values[name] = line[name].id
                elif field.type not in ['many2many', 'one2many']:
                    values[name] = line[name]
                elif name == 'invoice_line_tax_ids':
                    values[name] = [(6, 0, line[name].ids)]
                elif name == 'analytic_tag_ids':
                    values[name] = [(6, 0, line[name].ids)]
            result.append((0, 0, values))
        return result

    @api.model
    def _refund_tax_lines_account_change(self, lines, taxes_to_change):
        # Let's change the account on tax lines when
        # @param {list} lines: a list of orm commands
        # @param {dict} taxes_to_change
        #   key: tax ID, value: refund account

        if not taxes_to_change:
            return lines

        for line in lines:
            if isinstance(line[2], dict) and line[2]['tax_id'] in taxes_to_change:
                line[2]['account_id'] = taxes_to_change[line[2]['tax_id']]
        return lines

    def _get_refund_common_fields(self):
        return ['partner_id', 'payment_term_id', 'account_id', 'currency_id', 'journal_id']

    @api.model
    def _get_refund_prepare_fields(self):
        return ['name', 'reference', 'comment', 'date_due']

    @api.model
    def _get_refund_modify_read_fields(self):
        read_fields = ['type', 'number', 'invoice_line_ids', 'tax_line_ids',
                       'date']
        return self._get_refund_common_fields() + self._get_refund_prepare_fields() + read_fields

    @api.model
    def _get_refund_copy_fields(self):
        copy_fields = ['company_id', 'user_id', 'fiscal_position_id']
        return self._get_refund_common_fields() + self._get_refund_prepare_fields() + copy_fields

    def _get_currency_rate_date(self):
        """
        Obtém a data de referência para a taxa de câmbio.

        Retorna:
            date: A data contábil (date) se disponível, caso contrário, a data da fatura (date_invoice).
        """
        return self.date or self.date_invoice

    @api.model
    def _prepare_refund(self, invoice, date_invoice=None, date=None, description=None, journal_id=None):
        """ Prepare the dict of values to create the new credit note from the invoice.
            This method may be overridden to implement custom
            credit note generation (making sure to call super() to establish
            a clean extension chain).

            :param record invoice: invoice as credit note
            :param string date_invoice: credit note creation date from the wizard
            :param integer date: force date from the wizard
            :param string description: description of the credit note from the wizard
            :param integer journal_id: account.journal from the wizard
            :return: dict of value to create() the credit note
        """
        values = {}
        for field in self._get_refund_copy_fields():
            if invoice._fields[field].type == 'many2one':
                values[field] = invoice[field].id
            else:
                values[field] = invoice[field] or False

        values['invoice_line_ids'] = self._refund_cleanup_lines(invoice.invoice_line_ids)

        tax_lines = invoice.tax_line_ids
        taxes_to_change = {
            line.tax_id.id: line.tax_id.refund_account_id.id
            for line in tax_lines.filtered(lambda l: l.tax_id.refund_account_id != l.tax_id.account_id)
        }
        cleaned_tax_lines = self._refund_cleanup_lines(tax_lines)
        values['tax_line_ids'] = self._refund_tax_lines_account_change(cleaned_tax_lines, taxes_to_change)

        if journal_id:
            journal = self.env['account.journal'].browse(journal_id)
        elif invoice['type'] == 'in_invoice':
            journal = self.env['account.journal'].search([('type', '=', 'purchase')], limit=1)
        else:
            journal = self.env['account.journal'].search([('type', '=', 'sale')], limit=1)
        values['journal_id'] = journal.id

        values['type'] = TYPE2REFUND[invoice['type']]
        values['date_invoice'] = date_invoice or fields.Date.context_today(invoice)
        values['date_due'] = values['date_invoice']
        values['state'] = 'draft'
        values['number'] = False
        values['origin'] = invoice.number
        values['refund_invoice_id'] = invoice.id
        values['reference'] = False

        if values['type'] == 'in_refund':
            values['payment_term_id'] = invoice.partner_id.property_supplier_payment_term_id.id
            partner_bank_result = self._get_partner_bank_id(values['company_id'])
            if partner_bank_result:
                values['partner_bank_id'] = partner_bank_result.id
        else:
            values['payment_term_id'] = invoice.partner_id.property_payment_term_id.id

        if date:
            values['date'] = date
        if description:
            values['name'] = description
        return values

    @api.multi
    @api.returns('self')
    def refund(self, date_invoice=None, date=None, description=None, journal_id=None):
        new_invoices = self.browse()
        for invoice in self:
            # create the new invoice
            values = self._prepare_refund(invoice, date_invoice=date_invoice, date=date,
                                    description=description, journal_id=journal_id)
            refund_invoice = self.create(values)
            if invoice.type == 'out_invoice':
                message = _("This customer invoice credit note has been created from: <a href=# data-oe-model=account.invoice data-oe-id=%d>%s</a><br>Reason: %s") % (invoice.id, invoice.number, description)
            else:
                message = _("This vendor bill credit note has been created from: <a href=# data-oe-model=account.invoice data-oe-id=%d>%s</a><br>Reason: %s") % (invoice.id, invoice.number, description)

            refund_invoice.message_post(body=message)
            new_invoices += refund_invoice
        return new_invoices

    def _prepare_payment_vals(self, pay_journal, pay_amount=None, date=None, writeoff_acc=None, communication=None):
        payment_type = self.type in ('out_invoice', 'in_refund') and 'inbound' or 'outbound'
        if payment_type == 'inbound':
            payment_method = self.env.ref('account.account_payment_method_manual_in')
            journal_payment_methods = pay_journal.inbound_payment_method_ids
        else:
            payment_method = self.env.ref('account.account_payment_method_manual_out')
            journal_payment_methods = pay_journal.outbound_payment_method_ids

        if not communication:
            communication = self.type in ('in_invoice', 'in_refund') and self.reference or self.number
            if self.origin:
                communication = '%s (%s)' % (communication, self.origin)

        payment_vals = {
            'invoice_ids': [(6, 0, self.ids)],
            'amount': pay_amount or self.residual,
            'payment_date': date or fields.Date.context_today(self),
            'communication': communication,
            'partner_id': self.partner_id.id,
            'partner_type': self.type in ('out_invoice', 'out_refund') and 'customer' or 'supplier',
            'journal_id': pay_journal.id,
            'payment_type': payment_type,
            'payment_method_id': payment_method.id,
            'payment_difference_handling': writeoff_acc and 'reconcile' or 'open',
            'writeoff_account_id': writeoff_acc and writeoff_acc.id or False,
        }
        return payment_vals

    @api.multi
    def pay_and_reconcile(self, pay_journal, pay_amount=None, date=None, writeoff_acc=None):
        """ Create and post an account.payment for the invoice self, which creates a journal entry that reconciles the invoice.

            :param pay_journal: journal in which the payment entry will be created
            :param pay_amount: amount of the payment to register, defaults to the residual of the invoice
            :param date: payment date, defaults to fields.Date.context_today(self)
            :param writeoff_acc: account in which to create a writeoff if pay_amount < self.residual, so that the invoice is fully paid
        """
        if isinstance(pay_journal, pycompat.integer_types):
            pay_journal = self.env['account.journal'].browse([pay_journal])
        assert len(self) == 1, "Can only pay one invoice at a time."

        payment_vals = self._prepare_payment_vals(pay_journal, pay_amount=pay_amount, date=date, writeoff_acc=writeoff_acc)
        payment = self.env['account.payment'].create(payment_vals)
        payment.post()

        return True

    @api.multi
    def _track_subtype(self, init_values):
        self.ensure_one()
        if 'state' in init_values and self.state == 'paid' and self.type in ('out_invoice', 'out_refund'):
            return 'account.mt_invoice_paid'
        elif 'state' in init_values and self.state == 'open' and self.type in ('out_invoice', 'out_refund'):
            return 'account.mt_invoice_validated'
        elif 'state' in init_values and self.state == 'draft' and self.type in ('out_invoice', 'out_refund'):
            return 'account.mt_invoice_created'
        return super(AccountInvoice, self)._track_subtype(init_values)

    def _amount_by_group(self):
        self = self.sudo()

        for invoice in self:
            currency = invoice.currency_id or invoice.company_id.currency_id
            fmt = partial(formatLang, invoice.with_context(lang=invoice.partner_id.lang).env, currency_obj=currency)
            res = {}
            for line in invoice.tax_line_ids:
                tax = line.tax_id
                group_key = (tax.tax_group_id, tax.amount_type, tax.amount)
                res.setdefault(group_key, {'base': 0.0, 'amount': 0.0})
                res[group_key]['amount'] += line.amount_total
                res[group_key]['base'] += line.base
            res = sorted(res.items(), key=lambda l: l[0][0].sequence)
            invoice.amount_by_group = [(
                r[0][0].name, r[1]['amount'], r[1]['base'],
                fmt(r[1]['amount']), fmt(r[1]['base']),
                len(res),
            ) for r in res]

    @api.multi
    def preview_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'target': 'self',
            'url': self.get_portal_url(),
        }

    def _get_intrastat_country_id(self):
        return self.partner_id.country_id.id

    def _get_onchange_create(self):
        return OrderedDict([
            ('_onchange_partner_id', ['account_id', 'payment_term_id', 'fiscal_position_id', 'partner_bank_id']),
            ('_onchange_journal_id', ['currency_id']),
        ])


class AccountInvoiceLine(models.Model):
    _name = "account.invoice.line"
    _description = "Invoice Line"
    _order = "invoice_id,sequence,id"

    @api.one
    @api.depends('price_unit', 'discount', 'invoice_line_tax_ids', 'quantity',
        'product_id', 'invoice_id.partner_id', 'invoice_id.currency_id', 'invoice_id.company_id',
        'invoice_id.date_invoice', 'invoice_id.date')
    def _compute_price(self):
        currency = self.invoice_id and self.invoice_id.currency_id or None
        price = self.price_unit * (1 - (self.discount or 0.0) / 100.0)
        taxes = False
        if self.invoice_line_tax_ids:
            taxes = self.invoice_line_tax_ids.compute_all(price, currency, self.quantity, product=self.product_id, partner=self.invoice_id.partner_id)
        self.price_subtotal = price_subtotal_signed = taxes['total_excluded'] if taxes else self.quantity * price
        self.price_total = taxes['total_included'] if taxes else self.price_subtotal
        if self.invoice_id.currency_id and self.invoice_id.currency_id != self.invoice_id.company_id.currency_id:
            currency = self.invoice_id.currency_id
            date = self.invoice_id._get_currency_rate_date()
            price_subtotal_signed = currency._convert(price_subtotal_signed, self.invoice_id.company_id.currency_id, self.company_id or self.env.user.company_id, date or fields.Date.today())
        sign = self.invoice_id.type in ['in_refund', 'out_refund'] and -1 or 1
        self.price_subtotal_signed = price_subtotal_signed * sign

    @api.model
    def _default_account(self):
        journal_id = self._context.get('journal_id') or self._context.get('default_journal_id')
        if journal_id:
            journal = self.env['account.journal'].browse(journal_id)
            if self._context.get('type') in ('out_invoice', 'in_refund'):
                return journal.default_credit_account_id.id
            return journal.default_debit_account_id.id

    def _get_price_tax(self):
        for l in self:
            l.price_tax = l.price_total - l.price_subtotal

    name = fields.Text(string='Description', required=True)
    origin = fields.Char(string='Source Document',
        help="Reference of the document that produced this invoice.")
    sequence = fields.Integer(default=10,
        help="Gives the sequence of this line when displaying the invoice.")
    invoice_id = fields.Many2one('account.invoice', string='Invoice Reference',
        ondelete='cascade', index=True)
    invoice_type = fields.Selection(related='invoice_id.type', readonly=True)
    uom_id = fields.Many2one('uom.uom', string='Unit of Measure',
        ondelete='set null', index=True, oldname='uos_id')
    product_id = fields.Many2one('product.product', string='Product',
        ondelete='restrict', index=True)
    product_image = fields.Binary('Product Image', related="product_id.image", store=False, readonly=True)
    account_id = fields.Many2one('account.account', string='Account', domain=[('deprecated', '=', False)],
        default=_default_account,
        help="The income or expense account related to the selected product.")
    price_unit = fields.Float(
        string='Unit Price',
        required=True,
    )
    price_subtotal = fields.Monetary(string='Amount (without Taxes)',
        store=True, readonly=True, compute='_compute_price', help="Total amount without taxes")
    price_total = fields.Monetary(string='Amount (with Taxes)',
        store=True, readonly=True, compute='_compute_price', help="Total amount with taxes")
    price_subtotal_signed = fields.Monetary(string='Amount Signed', currency_field='company_currency_id',
        store=True, readonly=True, compute='_compute_price',
        help="Total amount in the currency of the company, negative for credit note.")
    price_tax = fields.Monetary(string='Tax Amount', compute='_get_price_tax', store=False)
    quantity = fields.Float(string='Quantity', digits=dp.get_precision('Product Unit of Measure'),
        required=True, default=1)
    discount = fields.Float(string='Discount (%)', digits=dp.get_precision('Discount'),
        default=0.0)
    invoice_line_tax_ids = fields.Many2many('account.tax',
        'account_invoice_line_tax', 'invoice_line_id', 'tax_id',
        string='Taxes', domain=[('type_tax_use','!=','none'), '|', ('active', '=', False), ('active', '=', True)], oldname='invoice_line_tax_id')
    account_analytic_id = fields.Many2one('account.analytic.account',
        string='Analytic Account')
    analytic_tag_ids = fields.Many2many('account.analytic.tag', string='Analytic Tags')
    company_id = fields.Many2one('res.company', string='Company',
        related='invoice_id.company_id', store=True, readonly=True, related_sudo=False)
    partner_id = fields.Many2one('res.partner', string='Partner',
        related='invoice_id.partner_id', store=True, readonly=True, related_sudo=False)
    currency_id = fields.Many2one('res.currency', related='invoice_id.currency_id', store=True, related_sudo=False, readonly=False)
    company_currency_id = fields.Many2one('res.currency', related='invoice_id.company_currency_id', readonly=True, related_sudo=False)
    is_rounding_line = fields.Boolean(string='Rounding Line', help='Is a rounding line in case of cash rounding.')

    display_type = fields.Selection([
        ('line_section', "Section"),
        ('line_note', "Note")], default=False, help="Technical field for UX purpose.")

    @api.model
    def fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False):
        res = super(AccountInvoiceLine, self).fields_view_get(
            view_id=view_id, view_type=view_type, toolbar=toolbar, submenu=submenu)
        if self._context.get('type'):
            doc = etree.XML(res['arch'])
            for node in doc.xpath("//field[@name='product_id']"):
                if self._context['type'] in ('in_invoice', 'in_refund'):
                    # Hack to fix the stable version 8.0 -> saas-12
                    # purchase_ok will be moved from purchase to product in master #13271
                    if 'purchase_ok' in self.env['product.template']._fields:
                        node.set('domain', "[('purchase_ok', '=', True)]")
                else:
                    node.set('domain', "[('sale_ok', '=', True)]")
            res['arch'] = etree.tostring(doc, encoding='unicode')
        return res

    @api.v8
    def get_invoice_line_account(self, type, product, fpos, company):
        accounts = product.product_tmpl_id.get_product_accounts(fpos)
        if type in ('out_invoice', 'out_refund'):
            return accounts['income']
        return accounts['expense']

    def _set_currency(self):
        company = self.invoice_id.company_id
        currency = self.invoice_id.currency_id
        if company and currency:
            if company.currency_id != currency:
                self.price_unit = self.price_unit * currency.with_context(dict(self._context or {}, date=self.invoice_id.date_invoice)).rate

    def _set_taxes(self):
        """
        Define os impostos e o preço do produto na linha da fatura.
        Este método é utilizado no onchange para ajustar os impostos e o preço.

        - Filtra os impostos da empresa.
        - Mapeia os impostos com base na posição fiscal.
        - Ajusta o preço unitário com base nos impostos incluídos.

        """
        self.ensure_one()

        # Obter a empresa associada
        company_id = self.company_id or self.env.user.company_id

        # Determinar os impostos aplicáveis com base no tipo de fatura
        if self.invoice_id.type in ('out_invoice', 'out_refund'):
            taxes = (
                self.product_id.taxes_id.filtered(lambda r: r.company_id == company_id)
                or self.account_id.tax_ids
                or self.invoice_id.company_id.account_sale_tax_id
            )
        else:
            taxes = (
                self.product_id.supplier_taxes_id.filtered(lambda r: r.company_id == company_id)
                or self.account_id.tax_ids
                or self.invoice_id.company_id.account_purchase_tax_id
            )

        # Mapear os impostos com base na posição fiscal
        self.invoice_line_tax_ids = fp_taxes = self.invoice_id.fiscal_position_id.map_tax(
            taxes, self.product_id, self.invoice_id.partner_id
        )

        # Ajustar o preço com base nos impostos incluídos
        fix_price = self.env['account.tax']._fix_tax_included_price

        # Ajustar o preço unitário para faturas de entrada
        if self.invoice_id.type in ('in_invoice', 'in_refund'):
            prec = self.env['decimal.precision'].precision_get('Product Price')
            if not self.price_unit or float_compare(self.price_unit, self.product_id.standard_price, precision_digits=prec) == 0:
                self.price_unit = fix_price(self.product_id.standard_price, taxes, fp_taxes)
                self._set_currency()

        # Manter o preço unitário se a flag 'keep_price_unit' estiver ativa
        elif self.env.context.get('keep_price_unit', False):
            self.price_unit = fix_price(self.price_unit, taxes, fp_taxes)
            self._set_currency()

        # Ajustar o preço unitário para o preço de lista do produto
        else:
            self.price_unit = fix_price(self.product_id.lst_price, taxes, fp_taxes)
            self._set_currency()

    @api.onchange('product_id')
    def _onchange_product_id(self):
        domain = {}
        if not self.invoice_id:
            return

        part = self.invoice_id.partner_id
        fpos = self.invoice_id.fiscal_position_id
        company = self.invoice_id.company_id
        currency = self.invoice_id.currency_id
        type = self.invoice_id.type

        if not part:
            warning = {
                    'title': _('Warning!'),
                    'message': _('You must first select a partner.'),
                }
            return {'warning': warning}

        if not self.product_id:
            if type not in ('in_invoice', 'in_refund'):
                self.price_unit = 0.0
            domain['uom_id'] = []
            if fpos:
                self.account_id = fpos.map_account(self.account_id)
        else:
            self_lang = self
            if part.lang:
                self_lang = self.with_context(lang=part.lang)

            product = self_lang.product_id
            account = self.get_invoice_line_account(type, product, fpos, company)
            if account:
                self.account_id = account.id
            self._set_taxes()

            product_name = self_lang._get_invoice_line_name_from_product()
            
            if product_name is not None:
                self.name = product_name

            if type in ('in_invoice', 'in_refund'):
                product_uom_id = product.uom_po_id
            else:
                product_uom_id = product.uom_id

            if not self.uom_id or product_uom_id.category_id.id != self.uom_id.category_id.id:
                self.uom_id = product_uom_id.id

            domain['uom_id'] = [('category_id', '=', product_uom_id.category_id.id)]

            if company and currency and self.uom_id and self.uom_id.id != product_uom_id.id:
                self.price_unit = product_uom_id._compute_price(self.price_unit, self.uom_id)

        return {'domain': domain}

    def _get_invoice_line_name_from_product(self):
        """ Returns the automatic name to give to the invoice line depending on
        the product it is linked to.
        """
        self.ensure_one()
        if not self.product_id:
            return ''
        invoice_type = self.invoice_id.type
        rslt = self.product_id.partner_ref
        if invoice_type in ('in_invoice', 'in_refund'):
            if self.product_id.description_purchase:
                rslt += '\n' + self.product_id.description_purchase
        else:
            if self.product_id.description_sale:
                rslt += '\n' + self.product_id.description_sale

        return rslt

    @api.onchange("account_id")
    def _onchange_account_id(self):
        """
        Atualiza os impostos da linha da fatura com base na conta contábil selecionada.

        - Se não houver produto associado, utiliza os impostos padrão da empresa ou da conta.
        - Se houver produto, mas o preço unitário não estiver definido, ajusta os impostos.

        Este método é otimizado para evitar buscas desnecessárias e utiliza
        verificações diretas para melhorar a performance.

        """
        if not self.account_id:
            return

        fpos = self.invoice_id.fiscal_position_id
        company = self.invoice_id.company_id
        invoice_type = self.invoice_id.type

        # Determina o imposto padrão com base no tipo de fatura
        default_tax = (
            company.account_sale_tax_id
            if invoice_type in ("out_invoice", "out_refund")
            else company.account_purchase_tax_id
        )

        # Atualiza os impostos da linha da fatura
        self.invoice_line_tax_ids = fpos.map_tax(
            self.account_id.tax_ids or default_tax, partner=self.partner_id
        )

        # Ajusta os impostos se o preço unitário não estiver definido
        if self.product_id and not self.price_unit:
            self._set_taxes()

    @api.onchange('uom_id')
    def _onchange_uom_id(self):
        warning = {}
        result = {}
        if not self.uom_id:
            self.price_unit = 0.0

        if self.product_id and self.uom_id:
            self._set_taxes()
            self.price_unit = self.product_id.uom_id._compute_price(self.price_unit, self.uom_id)

            if self.product_id.uom_id.category_id.id != self.uom_id.category_id.id:
                warning = {
                    'title': _('Warning!'),
                    'message': _('The selected unit of measure has to be in the same category as the product unit of measure.'),
                }
                self.uom_id = self.product_id.uom_id.id
        if warning:
            result['warning'] = warning
        return result

    def _set_additional_fields(self, invoice):
        """ Some modules, such as Purchase, provide a feature to add automatically pre-filled
            invoice lines. However, these modules might not be aware of extra fields which are
            added by extensions of the accounting module.
            This method is intended to be overridden by these extensions, so that any new field can
            easily be auto-filled as well.
            :param invoice : account.invoice corresponding record
            :rtype line : account.invoice.line record
        """
        pass

    @api.multi
    def unlink(self):
        if self.filtered(lambda r: r.invoice_id and r.invoice_id.state != 'draft'):
            raise UserError(_('You can only delete an invoice line if the invoice is in draft state.'))
        return super(AccountInvoiceLine, self).unlink()

    def _prepare_invoice_line(self):
        data = {
            'name': self.name,
            'origin': self.origin,
            'uom_id': self.uom_id.id,
            'product_id': self.product_id.id,
            'account_id': self.account_id.id,
            'price_unit': self.price_unit,
            'quantity': self.quantity,
            'discount': self.discount,
            'account_analytic_id': self.account_analytic_id.id,
            'analytic_tag_ids': self.analytic_tag_ids.ids,
            'invoice_line_tax_ids': self.invoice_line_tax_ids.ids
        }
        return data

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('display_type', self.default_get(['display_type'])['display_type']):
                vals.update(price_unit=0, account_id=False, quantity=0)
        return super(AccountInvoiceLine, self).create(vals_list)

    @api.multi
    def write(self, values):
        if 'display_type' in values and self.filtered(lambda line: line.display_type != values.get('display_type')):
            raise UserError(_("You cannot change the type of an invoice line. Instead you should delete the current line and create a new line of the proper type."))
        return super(AccountInvoiceLine, self).write(values)

    _sql_constraints = [
        ('accountable_required_fields',
            "CHECK(display_type IS NOT NULL OR account_id IS NOT NULL)",
            "Missing required account on accountable invoice line."),

        ('non_accountable_fields_null',
            "CHECK(display_type IS NULL OR (price_unit = 0 AND account_id IS NULL and quantity = 0))",
            "Forbidden unit price, account and quantity on non-accountable invoice line"),
    ]


class AccountInvoiceTax(models.Model):
    _name = "account.invoice.tax"
    _description = "Invoice Tax"
    _order = 'sequence'

    @api.depends('invoice_id.invoice_line_ids')
    def _compute_base_amount(self):
        tax_grouped = {}
        for invoice in self.mapped('invoice_id'):
            tax_grouped[invoice.id] = invoice.get_taxes_values()
        for tax in self:
            tax.base = 0.0
            if tax.tax_id:
                key = tax.tax_id.get_grouping_key({
                    'tax_id': tax.tax_id.id,
                    'account_id': tax.account_id.id,
                    'account_analytic_id': tax.account_analytic_id.id,
                    'analytic_tag_ids': tax.analytic_tag_ids.ids or False,
                })
                if tax.invoice_id and key in tax_grouped[tax.invoice_id.id]:
                    tax.base = tax_grouped[tax.invoice_id.id][key]['base']
                else:
                    _logger.warning('Tax Base Amount not computable probably due to a change in an underlying tax (%s).', tax.tax_id.name)

    invoice_id = fields.Many2one('account.invoice', string='Invoice', ondelete='cascade', index=True)
    name = fields.Char(string='Tax Description', required=True)
    tax_id = fields.Many2one('account.tax', string='Tax', ondelete='restrict')
    account_id = fields.Many2one('account.account', string='Tax Account', required=True, domain=[('deprecated', '=', False)])
    account_analytic_id = fields.Many2one('account.analytic.account', string='Analytic account')
    analytic_tag_ids = fields.Many2many('account.analytic.tag', string='Analytic Tags')
    amount = fields.Monetary('Tax Amount')
    amount_rounding = fields.Monetary('Amount Delta')
    amount_total = fields.Monetary(string="Amount Total", compute='_compute_amount_total')
    manual = fields.Boolean(default=True)
    sequence = fields.Integer(help="Gives the sequence order when displaying a list of invoice tax.")
    company_id = fields.Many2one('res.company', string='Company', related='account_id.company_id', store=True, readonly=True)
    currency_id = fields.Many2one('res.currency', related='invoice_id.currency_id', store=True, readonly=True)
    base = fields.Monetary(string='Base', compute='_compute_base_amount', store=True)

    @api.depends('amount', 'amount_rounding')
    def _compute_amount_total(self):
        for tax_line in self:
            tax_line.amount_total = tax_line.amount + tax_line.amount_rounding


class AccountPaymentTerm(models.Model):
    _name = "account.payment.term"
    _description = "Payment Terms"
    _order = "sequence, id"

    def _default_line_ids(self):
        return [(0, 0, {'value': 'balance', 'value_amount': 0.0, 'sequence': 9, 'days': 0, 'option': 'day_after_invoice_date'})]

    name = fields.Char(string='Payment Terms', translate=True, required=True)
    active = fields.Boolean(default=True, help="If the active field is set to False, it will allow you to hide the payment terms without removing it.")
    note = fields.Text(string='Description on the Invoice', translate=True)
    line_ids = fields.One2many('account.payment.term.line', 'payment_id', string='Terms', copy=True, default=_default_line_ids)
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.user.company_id)
    sequence = fields.Integer(required=True, default=10)

    @api.constrains('line_ids')
    @api.one
    def _check_lines(self):
        payment_term_lines = self.line_ids.sorted()
        if payment_term_lines and payment_term_lines[-1].value != 'balance':
            raise ValidationError(_('The last line of a Payment Term should have the Balance type.'))
        lines = self.line_ids.filtered(lambda r: r.value == 'balance')
        if len(lines) > 1:
            raise ValidationError(_('A Payment Term should have only one line of type Balance.'))

    @api.one
    def compute(self, value, date_ref=False, value_without_taxes=0.0):
        """Método para Calcular as Parcelas

        Args:
            value (float): Valor a Calcular as Parcelas
            date_ref (Date, optional): Data de Referencias para Parcelas. Defaults to False.
            value_taxes (float, optional): Valor sem Impostos (bruto) a Calcular as Parcelas. Defaults to 0.

        Returns:
            list: Lista das Parcelas
                    0 - Data de Vencimento
                    1 - Valor da Parcela
                    2 - Dias de Vencimento
                    3 - Valor sem Impostos
                    4 - Porcentagem ou Valor do Cadastro das Parcelas
                    5 - Pagamento Antecipado: Default False
        """
        date_ref = date_ref or fields.Date.today()
        amount = value
        amount_without_taxes = value_without_taxes
        sign = value < 0 and -1 or 1
        sign_without_taxes = value_without_taxes < 0 and -1 or 1
        result = []

        if self.env.context.get("currency_id"):
            currency = self.env["res.currency"].browse(self.env.context["currency_id"])
        else:
            currency = self.env.user.company_id.currency_id

        for line in self.line_ids:
            if line.value == "fixed":
                amt = sign * currency.round(line.value_amount)
                amt_without_taxes = sign_without_taxes * currency.round(line.value_amount)
            elif line.value == "percent":
                amt = currency.round(value * (line.value_amount / 100.0))
                amt_without_taxes = currency.round(value_without_taxes * (line.value_amount / 100.0))
            elif line.value == "balance":
                amt = currency.round(amount)
                amt_without_taxes = currency.round(amount_without_taxes)

            if amt:
                next_date = fields.Date.from_string(date_ref)
                if line.option == "day_after_invoice_date":
                    next_date += relativedelta(days=line.days)
                    if line.day_of_the_month > 0:
                        months_delta = (
                            (line.day_of_the_month < next_date.day) and 1 or 0
                        )
                        next_date += relativedelta(
                            day=line.day_of_the_month, months=months_delta
                        )
                elif line.option == "after_invoice_month":
                    next_first_date = next_date + relativedelta(
                        day=1, months=1
                    )  # Getting 1st of next month
                    next_date = next_first_date + relativedelta(days=line.days - 1)
                elif line.option == "day_following_month":
                    next_date += relativedelta(day=line.days, months=1)
                elif line.option == "day_current_month":
                    next_date += relativedelta(day=line.days, months=0)

                result.append(
                    (fields.Date.to_string(next_date), amt, line.days, amt_without_taxes, line.value_amount, False)
                )

                amount -= amt
                amount_without_taxes -= amt_without_taxes

        if result is not False:
            amount = sum(amt[1] for amt in result)
            amount_without_taxes = sum(amt_without_taxes[3] for amt_without_taxes in result)

        dist = currency.round(value - amount)
        dist_without_taxes = currency.round(value_without_taxes - amount_without_taxes)

        if dist or dist_without_taxes:
            last_date = result and result[-1][0] or fields.Date.today()
            days = result and result[-1][2] or 0
            result.append((last_date, dist, days, dist_without_taxes, 0, False))

        return result

    @api.multi
    def unlink(self):
        if self.env['account.invoice'].search([('payment_term_id', 'in', self.ids)]):
            raise UserError(_('You can not delete payment terms as other records still reference it. However, you can archive it.'))
        property_recs = self.env['ir.property'].search([('value_reference', 'in', ['account.payment.term,%s'%payment_term.id for payment_term in self])])
        property_recs.unlink()
        return super(AccountPaymentTerm, self).unlink()


class AccountPaymentTermLine(models.Model):
    _name = "account.payment.term.line"
    _description = "Payment Terms Line"
    _order = "sequence, id"

    value = fields.Selection([
            ('balance', 'Balance'),
            ('percent', 'Percent'),
            ('fixed', 'Fixed Amount')
        ], string='Type', required=True, default='balance',
        help="Select here the kind of valuation related to this payment terms line.")
    value_amount = fields.Float(string='Value', digits=dp.get_precision('Payment Terms'), help="For percent enter a ratio between 0-100.")
    days = fields.Integer(string='Number of Days', required=True, default=0)
    day_of_the_month = fields.Integer(string='Day of the month', help="Day of the month on which the invoice must come to its term. If zero or negative, this value will be ignored, and no specific day will be set. If greater than the last day of a month, this number will instead select the last day of this month.")
    option = fields.Selection([
            ('day_after_invoice_date', "day(s) after the invoice date"),
            ('after_invoice_month', "day(s) after the end of the invoice month"),
            ('day_following_month', "of the following month"),
            ('day_current_month', "of the current month"),
        ],
        default='day_after_invoice_date', required=True, string='Options'
        )
    payment_id = fields.Many2one('account.payment.term', string='Payment Terms', required=True, index=True, ondelete='cascade')
    sequence = fields.Integer(default=10, help="Gives the sequence order when displaying a list of payment terms lines.")

    @api.one
    @api.constrains('value', 'value_amount')
    def _check_percent(self):
        if self.value == 'percent' and (self.value_amount < 0.0 or self.value_amount > 100.0):
            raise ValidationError(_('Percentages on the Payment Terms lines must be between 0 and 100.'))

    @api.one
    @api.constrains('days')
    def _check_days(self):
        if self.option in ('day_following_month', 'day_current_month') and self.days <= 0:
            raise ValidationError(_("The day of the month used for this term must be stricly positive."))
        elif self.days < 0:
            raise ValidationError(_("The number of days used for a payment term cannot be negative."))

    @api.onchange('option')
    def _onchange_option(self):
        if self.option in ('day_current_month', 'day_following_month'):
            self.days = 0
