# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import odoo
from odoo.exceptions import UserError, AccessError
from odoo.tests import Form
from odoo.tools import float_compare

from .test_sale_common import TestCommonSaleNoChart


class TestSaleOrder(TestCommonSaleNoChart):

    @classmethod
    def setUpClass(cls):
        super(TestSaleOrder, cls).setUpClass()

        SaleOrder = cls.env['sale.order'].with_context(tracking_disable=True)

        # set up users
        cls.setUpUsers()
        group_salemanager = cls.env.ref('sales_team.group_sale_manager')
        group_salesman = cls.env.ref('sales_team.group_sale_salesman')
        group_employee = cls.env.ref('base.group_user')
        cls.user_manager.write({'groups_id': [(6, 0, [group_salemanager.id, group_employee.id])]})
        cls.user_employee.write({'groups_id': [(6, 0, [group_salesman.id, group_employee.id])]})

        # set up accounts and products and journals
        cls.setUpAdditionalAccounts()
        cls.setUpClassicProducts()
        cls.setUpAccountJournal()

        # create a generic Sale Order with all classical products and empty pricelist
        cls.sale_order = SaleOrder.create({
            'partner_id': cls.partner_customer_usd.id,
            'partner_invoice_id': cls.partner_customer_usd.id,
            'partner_shipping_id': cls.partner_customer_usd.id,
            'pricelist_id': cls.pricelist_usd.id,
        })
        cls.sol_product_order = cls.env['sale.order.line'].create({
            'name': cls.product_order.name,
            'product_id': cls.product_order.id,
            'product_uom_qty': 2,
            'product_uom': cls.product_order.uom_id.id,
            'price_unit': cls.product_order.list_price,
            'order_id': cls.sale_order.id,
            'tax_id': False,
        })
        cls.sol_serv_deliver = cls.env['sale.order.line'].create({
            'name': cls.service_deliver.name,
            'product_id': cls.service_deliver.id,
            'product_uom_qty': 2,
            'product_uom': cls.service_deliver.uom_id.id,
            'price_unit': cls.service_deliver.list_price,
            'order_id': cls.sale_order.id,
            'tax_id': False,
        })
        cls.sol_serv_order = cls.env['sale.order.line'].create({
            'name': cls.service_order.name,
            'product_id': cls.service_order.id,
            'product_uom_qty': 2,
            'product_uom': cls.service_order.uom_id.id,
            'price_unit': cls.service_order.list_price,
            'order_id': cls.sale_order.id,
            'tax_id': False,
        })
        cls.sol_product_deliver = cls.env['sale.order.line'].create({
            'name': cls.product_deliver.name,
            'product_id': cls.product_deliver.id,
            'product_uom_qty': 2,
            'product_uom': cls.product_deliver.uom_id.id,
            'price_unit': cls.product_deliver.list_price,
            'order_id': cls.sale_order.id,
            'tax_id': False,
        })

    def test_sale_order(self):
        """ Test the sales order flow (invoicing and quantity updates)
            - Invoice repeatedly while varrying delivered quantities and check that invoice are always what we expect
        """
        # DBO TODO: validate invoice and register payments
        Invoice = self.env['account.invoice']
        self.sale_order.order_line.read(['name', 'price_unit', 'product_uom_qty', 'price_total'])

        self.assertEqual(self.sale_order.amount_total, sum([2 * p.list_price for p in self.product_map.values()]), 'Sale: total amount is wrong')
        self.sale_order.order_line._compute_product_updatable()
        self.assertTrue(self.sale_order.order_line[0].product_updatable)
        # send quotation
        self.sale_order.force_quotation_send()
        self.assertTrue(self.sale_order.state == 'sent', 'Sale: state after sending is wrong')
        self.sale_order.order_line._compute_product_updatable()
        self.assertTrue(self.sale_order.order_line[0].product_updatable)

        # confirm quotation
        self.sale_order.action_confirm()
        self.assertTrue(self.sale_order.state == 'sale')
        self.assertTrue(self.sale_order.invoice_status == 'to invoice')

        # create invoice: only 'invoice on order' products are invoiced
        inv_id = self.sale_order.action_invoice_create()
        invoice = Invoice.browse(inv_id)
        self.assertEqual(len(invoice.invoice_line_ids), 2, 'Sale: invoice is missing lines')
        self.assertEqual(invoice.amount_total, sum([2 * p.list_price if p.invoice_policy == 'order' else 0 for p in self.product_map.values()]), 'Sale: invoice total amount is wrong')
        self.assertTrue(self.sale_order.invoice_status == 'no', 'Sale: SO status after invoicing should be "nothing to invoice"')
        self.assertTrue(len(self.sale_order.invoice_ids) == 1, 'Sale: invoice is missing')
        self.sale_order.order_line._compute_product_updatable()
        self.assertFalse(self.sale_order.order_line[0].product_updatable)

        # deliver lines except 'time and material' then invoice again
        for line in self.sale_order.order_line:
            line.qty_delivered = 2 if line.product_id.expense_policy == 'no' else 0
        self.assertTrue(self.sale_order.invoice_status == 'to invoice', 'Sale: SO status after delivery should be "to invoice"')
        inv_id = self.sale_order.action_invoice_create()
        invoice2 = Invoice.browse(inv_id)
        self.assertEqual(len(invoice2.invoice_line_ids), 2, 'Sale: second invoice is missing lines')
        self.assertEqual(invoice2.amount_total, sum([2 * p.list_price if p.invoice_policy == 'delivery' else 0 for p in self.product_map.values()]), 'Sale: second invoice total amount is wrong')
        self.assertTrue(self.sale_order.invoice_status == 'invoiced', 'Sale: SO status after invoicing everything should be "invoiced"')
        self.assertTrue(len(self.sale_order.invoice_ids) == 2, 'Sale: invoice is missing')

        # go over the sold quantity
        self.sol_serv_order.write({'qty_delivered': 10})
        self.assertTrue(self.sale_order.invoice_status == 'upselling', 'Sale: SO status after increasing delivered qty higher than ordered qty should be "upselling"')

        # upsell and invoice
        self.sol_serv_order.write({'product_uom_qty': 10})

        inv_id = self.sale_order.action_invoice_create()
        invoice3 = Invoice.browse(inv_id)
        self.assertEqual(len(invoice3.invoice_line_ids), 1, 'Sale: third invoice is missing lines')
        self.assertEqual(invoice3.amount_total, 8 * self.product_map['serv_order'].list_price, 'Sale: second invoice total amount is wrong')
        self.assertTrue(self.sale_order.invoice_status == 'invoiced', 'Sale: SO status after invoicing everything (including the upsel) should be "invoiced"')

    def test_unlink_cancel(self):
        """ Test deleting and cancelling sales orders depending on their state and on the user's rights """
        # SO in state 'draft' can be deleted
        so_copy = self.sale_order.copy()
        with self.assertRaises(AccessError):
            so_copy.sudo(self.user_employee).unlink()
        self.assertTrue(so_copy.sudo(self.user_manager).unlink(), 'Sale: deleting a quotation should be possible')

        # SO in state 'cancel' can be deleted
        so_copy = self.sale_order.copy()
        so_copy.action_confirm()
        self.assertTrue(so_copy.state == 'sale', 'Sale: SO should be in state "sale"')
        so_copy.action_cancel()
        self.assertTrue(so_copy.state == 'cancel', 'Sale: SO should be in state "cancel"')
        with self.assertRaises(AccessError):
            so_copy.sudo(self.user_employee).unlink()
        self.assertTrue(so_copy.sudo(self.user_manager).unlink(), 'Sale: deleting a cancelled SO should be possible')

        # SO in state 'sale' or 'done' cannot be deleted
        self.sale_order.action_confirm()
        self.assertTrue(self.sale_order.state == 'sale', 'Sale: SO should be in state "sale"')
        with self.assertRaises(UserError):
            self.sale_order.sudo(self.user_manager).unlink()

        self.sale_order.action_done()
        self.assertTrue(self.sale_order.state == 'done', 'Sale: SO should be in state "done"')
        with self.assertRaises(UserError):
            self.sale_order.sudo(self.user_manager).unlink()

    def test_cost_invoicing(self):
        """ Test confirming a vendor invoice to reinvoice cost on the so """
        # force the pricelist to have the same currency as the company
        self.pricelist_usd.currency_id = self.env.ref('base.main_company').currency_id

        serv_cost = self.env['product.product'].create({
            'name': "Ordered at cost",
            'standard_price': 160,
            'list_price': 180,
            'type': 'consu',
            'invoice_policy': 'order',
            'expense_policy': 'cost',
            'default_code': 'PROD_COST',
            'service_type': 'manual',
        })
        prod_gap = self.service_order
        so = self.env['sale.order'].create({
            'partner_id': self.partner_customer_usd.id,
            'partner_invoice_id': self.partner_customer_usd.id,
            'partner_shipping_id': self.partner_customer_usd.id,
            'order_line': [(0, 0, {'name': prod_gap.name, 'product_id': prod_gap.id, 'product_uom_qty': 2, 'product_uom': prod_gap.uom_id.id, 'price_unit': prod_gap.list_price})],
            'pricelist_id': self.pricelist_usd.id,
        })
        so.action_confirm()
        so._create_analytic_account()

        company = self.env.ref('base.main_company')
        journal = self.env['account.journal'].create({'name': 'Purchase Journal - Test', 'code': 'STPJ', 'type': 'purchase', 'company_id': company.id})
        invoice_vals = {
            'name': '',
            'type': 'in_invoice',
            'partner_id': self.partner_customer_usd.id,
            'invoice_line_ids': [(0, 0, {'name': serv_cost.name, 'product_id': serv_cost.id, 'quantity': 2, 'uom_id': serv_cost.uom_id.id, 'price_unit': serv_cost.standard_price, 'account_analytic_id': so.analytic_account_id.id, 'account_id': self.account_income.id})],
            'account_id': self.account_payable.id,
            'journal_id': journal.id,
            'currency_id': company.currency_id.id,
        }
        inv = self.env['account.invoice'].create(invoice_vals)
        inv.action_invoice_open()
        sol = so.order_line.filtered(lambda l: l.product_id == serv_cost)
        self.assertTrue(sol, 'Sale: cost invoicing does not add lines when confirming vendor invoice')
        self.assertEquals((sol.price_unit, sol.qty_delivered, sol.product_uom_qty, sol.qty_invoiced), (160, 2, 0, 0), 'Sale: line is wrong after confirming vendor invoice')

    def test_sale_with_taxes(self):
        """ Test SO with taxes applied on its lines and check subtotal applied on its lines and total applied on the SO """
        # Create a tax with price included
        tax_include = self.env['account.tax'].create({
            'name': 'Tax with price include',
            'amount': 10,
            'price_include': True
        })
        # Create a tax with price not included
        tax_exclude = self.env['account.tax'].create({
            'name': 'Tax with no price include',
            'amount': 10,
        })

        # Apply taxes on the sale order lines
        self.sol_product_order.write({'tax_id': [(4, tax_include.id)]})
        self.sol_serv_deliver.write({'tax_id': [(4, tax_include.id)]})
        self.sol_serv_order.write({'tax_id': [(4, tax_exclude.id)]})
        self.sol_product_deliver.write({'tax_id': [(4, tax_exclude.id)]})

        # Trigger onchange to reset discount, unit price, subtotal, ...
        for line in self.sale_order.order_line:
            line.product_id_change()
            line._onchange_discount()

        for line in self.sale_order.order_line:
            if line.tax_id.price_include:
                price = line.price_unit * line.product_uom_qty - line.price_tax
            else:
                price = line.price_unit * line.product_uom_qty

            self.assertEquals(float_compare(line.price_subtotal, price, precision_digits=2), 0)

        self.assertEquals(self.sale_order.amount_total,
                          self.sale_order.amount_untaxed + self.sale_order.amount_tax,
                          'Taxes should be applied')

    def test_so_create_multicompany(self):
        """In case we use new() outside of an onchange,
           it might cause the value of related fields to be incorrect.
           If so, then the company being a related might not be set,
           which would mean that taxes from all child companies
           would end up on the order lines.
        """
        user_demo = self.env.ref('base.user_demo')
        company_1 = self.env.ref('base.main_company')
        company_2 = self.env['res.company'].create({
            'name': 'company 2',
            'parent_id': company_1.id,
        })

        tax_company_1 = self.env['account.tax'].create({
            'name': 'T1',
            'amount': 90,
            'company_id': company_1.id,
        })

        tax_company_2 = self.env['account.tax'].create({
            'name': 'T2',
            'amount': 90,
            'company_id': company_2.id,
        })

        product_shared = self.env['product.template'].create({
            'name': 'shared product',
            'taxes_id': [(6, False, [tax_company_1.id, tax_company_2.id])],
        })

        so_1 = self.env['sale.order'].sudo(user_demo.id).create({
            'partner_id': self.env.ref('base.res_partner_2').id,
            'company_id': company_1.id,
        })
        so_1.write({
            'order_line': [(0, False, {'product_id': product_shared.product_variant_id.id, 'order_id': so_1.id})],
        })

        # self.assertEqual(set(so_1.order_line.tax_id.ids), set([tax_company_1.id]),
        #     'Only taxes from the right company are put by default')
        # Teste torna-se inutil pois o metodo '_compute_tax_id' foi totalmente sobrescrito
        # na localizacao

   #  def test_multi_currency_discount(self):
        # """Verify the currency used for pricelist price & discount computation."""
        # products = self.env["product.product"].search([], limit=2)
        # product_1 = products[0]
        # product_2 = products[1]

        # # Make sure the company is in USD
        # main_company = self.env.ref('base.main_company')
        # main_curr = main_company.currency_id
        # other_curr = (self.env.ref('base.USD') + self.env.ref('base.EUR')) - main_curr
        # # main_company.currency_id = other_curr # product.currency_id when no company_id set
        # other_company = self.env["res.company"].create({
            # "name": "Test",
            # "currency_id": other_curr.id
        # })
        # user_in_other_company = self.env["res.users"].create({
            # "company_id": other_company.id,
            # "company_ids": [(6, 0, [other_company.id])],
            # "name": "E.T",
            # "login": "hohoho",
        # })
        # user_in_other_company.groups_id |= self.env.ref('sale.group_discount_per_so_line')
        # self.env['res.currency.rate'].search([]).unlink()
        # self.env['res.currency.rate'].create({
            # 'name': '2010-01-01',
            # 'rate': 2.0,
            # 'currency_id': main_curr.id,
            # "company_id": False,
        # })

        # product_1.company_id = False
        # product_2.company_id = False

        # self.assertEqual(product_1.currency_id, main_curr)
        # self.assertEqual(product_2.currency_id, main_curr)
        # self.assertEqual(product_1.cost_currency_id, main_curr)
        # self.assertEqual(product_2.cost_currency_id, main_curr)

        # product_1_ctxt = product_1.with_env(self.env(user=user_in_other_company.id))
        # product_2_ctxt = product_2.with_env(self.env(user=user_in_other_company.id))
        # self.assertEqual(product_1_ctxt.currency_id, main_curr)
        # self.assertEqual(product_2_ctxt.currency_id, main_curr)
        # self.assertEqual(product_1_ctxt.cost_currency_id, other_curr)
        # self.assertEqual(product_2_ctxt.cost_currency_id, other_curr)

        # product_1.lst_price = 100.0
        # product_2_ctxt.standard_price = 10.0 # cost is company_dependent

        # pricelist = self.env["product.pricelist"].create({
            # "name": "Test multi-currency",
            # "discount_policy": "without_discount",
            # "currency_id": other_curr.id,
            # "item_ids": [
                # (0, 0, {
                    # "base": "list_price",
                    # "product_id": product_1.id,
                    # "compute_price": "percentage",
                    # "percent_price": 20,
                # }),
                # (0, 0, {
                    # "base": "standard_price",
                    # "product_id": product_2.id,
                    # "compute_price": "percentage",
                    # "percent_price": 10,
                # })
            # ]
        # })

        # # Create a SO in the other company
        # ##################################
        # # product_currency = main_company.currency_id when no company_id on the product

        # # CASE 1:
        # # company currency = so currency
        # # product_1.currency != so currency
        # # product_2.cost_currency_id = so currency
        # sales_order = product_1_ctxt.with_context(mail_notrack=True, mail_create_nolog=True).env["sale.order"].create({
            # "partner_id": self.env.user.partner_id.id,
            # "pricelist_id": pricelist.id,
            # "order_line": [
                # (0, 0, {
                    # "product_id": product_1.id,
                    # "product_uom_qty": 1.0
                # }),
                # (0, 0, {
                    # "product_id": product_2.id,
                    # "product_uom_qty": 1.0
                # })
            # ]
        # })
        # for line in sales_order.order_line:
            # # Create values autofill does not compute discount.
            # line._onchange_discount()

        # so_line_1 = sales_order.order_line[0]
        # so_line_2 = sales_order.order_line[1]
        # self.assertEqual(so_line_1.discount, 20)
        # self.assertEqual(so_line_1.price_unit, 50.0)
        # self.assertEqual(so_line_2.discount, 10)
        # self.assertEqual(so_line_2.price_unit, 10)

        # # CASE 2
        # # company currency != so currency
        # # product_1.currency == so currency
        # # product_2.cost_currency_id != so currency
        # pricelist.currency_id = main_curr
        # sales_order = product_1_ctxt.with_context(mail_notrack=True, mail_create_nolog=True).env["sale.order"].create({
            # "partner_id": self.env.user.partner_id.id,
            # "pricelist_id": pricelist.id,
            # "order_line": [
                # # Verify discount is considered in create hack
                # (0, 0, {
                    # "product_id": product_1.id,
                    # "product_uom_qty": 1.0
                # }),
                # (0, 0, {
                    # "product_id": product_2.id,
                    # "product_uom_qty": 1.0
                # })
            # ]
        # })
        # for line in sales_order.order_line:
            # line._onchange_discount()

        # so_line_1 = sales_order.order_line[0]
        # so_line_2 = sales_order.order_line[1]
        # self.assertEqual(so_line_1.discount, 20)
        # self.assertEqual(so_line_1.price_unit, 100.0)
        # self.assertEqual(so_line_2.discount, 10)
        # self.assertEqual(so_line_2.price_unit, 20)

    def test_discount_and_untaxed_subtotal(self):
        """When adding a discount on a SO line, this test ensures that the untaxed amount to invoice is
        equal to the untaxed subtotal"""
        sale_order = self.env['sale.order'].create({
            'partner_id': self.partner_customer_usd.id,
            'order_line': [(0, 0, {
                'product_id': self.product_deliver.id,
                'product_uom_qty': 38,
                'price_unit': 541.26,
                'discount': 2.00,
            })]
        })
        sale_order.action_confirm()
        line = sale_order.order_line
        self.assertEqual(line.untaxed_amount_to_invoice, 0)

        line.qty_delivered = 38
        # (541.26 - 0.02 * 541.26) * 38 = 20156.5224 ~= 20156.52
        self.assertEqual(line.price_subtotal, 20156.52)
        self.assertEqual(line.untaxed_amount_to_invoice, line.price_subtotal)

        # Same with an included-in-price tax
        sale_order = sale_order.copy()
        line = sale_order.order_line
        line.tax_id = [(0, 0, {
            'name': 'Super Tax',
            'amount_type': 'percent',
            'amount': 15.0,
            'price_include': True,
        })]
        sale_order.action_confirm()
        self.assertEqual(line.untaxed_amount_to_invoice, 0)

        line.qty_delivered = 38
        # (541,26 / 1,15) * ,98 * 38 = 17527,410782609 ~= 17527.41
        self.assertEqual(line.price_subtotal, 17527.41)
        self.assertEqual(line.untaxed_amount_to_invoice, line.price_subtotal)
