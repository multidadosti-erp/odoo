# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from itertools import chain

from odoo import api, fields, models, tools, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_repr

from odoo.addons import decimal_precision as dp

from odoo.tools import pycompat


class Pricelist(models.Model):
    _name = "product.pricelist"
    _description = "Pricelist"
    _order = "sequence asc, id desc"

    def _get_default_currency_id(self):
        return self.env.user.company_id.currency_id.id

    def _get_default_item_ids(self):
        ProductPricelistItem = self.env['product.pricelist.item']
        vals = ProductPricelistItem.default_get(list(ProductPricelistItem._fields))
        vals.update(compute_price='formula')
        return [[0, False, vals]]

    name = fields.Char('Pricelist Name', required=True, translate=True)
    active = fields.Boolean('Active', default=True, help="If unchecked, it will allow you to hide the pricelist without removing it.")
    item_ids = fields.One2many(
        'product.pricelist.item', 'pricelist_id', 'Pricelist Items',
        copy=True, default=_get_default_item_ids)

    # Adicionado pela Multidados:
    # Copia campo de itens, para filtrar os itens que não tem
    # parceiro relacionado. Na view não é possível adicionar
    # o domain em campos One2many.
    base_item_ids = fields.One2many(
        'product.pricelist.item', 'pricelist_id', 'Base Items',
        domain=[('partner_id', '=', False)],
        copy=False)
    partner_item_ids = fields.One2many(
        'product.pricelist.item', 'pricelist_id', 'Partners Items',
        domain=[('partner_id', '!=', False)],
        copy=False)

    currency_id = fields.Many2one('res.currency', 'Currency', default=_get_default_currency_id, required=True)
    company_id = fields.Many2one('res.company', 'Company')

    sequence = fields.Integer(default=16)
    country_group_ids = fields.Many2many('res.country.group', 'res_country_group_pricelist_rel',
                                         'pricelist_id', 'res_country_group_id', string='Country Groups')

    @api.multi
    def name_get(self):
        return [(pricelist.id, '%s (%s)' % (pricelist.name, pricelist.currency_id.name)) for pricelist in self]

    @api.model
    def _name_search(self, name, args=None, operator='ilike', limit=100, name_get_uid=None):
        if name and operator == '=' and not args:
            # search on the name of the pricelist and its currency, opposite of name_get(),
            # Used by the magic context filter in the product search view.
            query_args = {'name': name, 'limit': limit, 'lang': self._context.get('lang') or 'en_US'}
            query = """SELECT p.id
                       FROM ((
                                SELECT pr.id, pr.name
                                FROM product_pricelist pr JOIN
                                     res_currency cur ON
                                         (pr.currency_id = cur.id)
                                WHERE pr.name || ' (' || cur.name || ')' = %(name)s
                            )
                            UNION (
                                SELECT tr.res_id as id, tr.value as name
                                FROM ir_translation tr JOIN
                                     product_pricelist pr ON (
                                        pr.id = tr.res_id AND
                                        tr.type = 'model' AND
                                        tr.name = 'product.pricelist,name' AND
                                        tr.lang = %(lang)s
                                     ) JOIN
                                     res_currency cur ON
                                         (pr.currency_id = cur.id)
                                WHERE tr.value || ' (' || cur.name || ')' = %(name)s
                            )
                        ) p
                       ORDER BY p.name"""
            if limit:
                query += " LIMIT %(limit)s"
            self._cr.execute(query, query_args)
            ids = [r[0] for r in self._cr.fetchall()]
            # regular search() to apply ACLs - may limit results below limit in some cases
            pricelist_ids = self._search([('id', 'in', ids)], limit=limit, access_rights_uid=name_get_uid)
            if pricelist_ids:
                return self.browse(pricelist_ids).name_get()
        return super(Pricelist, self)._name_search(name, args, operator=operator, limit=limit, name_get_uid=name_get_uid)

    def _compute_price_rule_multi(self, products_qty_partner, date=False, uom_id=False):
        """ Low-level method - Multi pricelist, multi products
        Returns: dict{product_id: dict{pricelist_id: (price, suitable_rule)} }"""
        if not self.ids:
            pricelists = self.search([])
        else:
            pricelists = self

        results = {}

        for pricelist in pricelists:
            subres = pricelist._compute_price_rule(
                products_qty_partner, date=date, uom_id=uom_id
            )
            for product_id, price in subres.items():
                results.setdefault(product_id, {})
                results[product_id][pricelist.id] = price

        return results

    @api.multi
    def _compute_price_rule(self, products_qty_partner, date=False, uom_id=False):
        """ Low-level method - Mono pricelist, multi products
        Returns: dict{product_id: (price, suitable_rule) for the given pricelist}

        Date in context can be a date, datetime, ...

            :param products_qty_partner: list of typles products, quantity, partner
            :param datetime date: validity date
            :param ID uom_id: intermediate unit of measure
        """
        self.ensure_one()
        if not date:
            date = self._context.get("date") or fields.Date.today()

        # boundary conditions differ if we have a datetime
        date = fields.Date.to_date(date)

        if not uom_id and self._context.get("uom"):
            uom_id = self._context["uom"]

        if uom_id:
            # rebrowse with uom if given
            products = [
                item[0].with_context(uom=uom_id) for item in products_qty_partner
            ]
            products_qty_partner = [
                (products[index], data_struct[1], data_struct[2])
                for index, data_struct in enumerate(products_qty_partner)
            ]
        else:
            products = [item[0] for item in products_qty_partner]

        if not products:
            return {}

        # Get the partner id
        partner_ids = [item[2].id for item in products_qty_partner if item[2]]
        partner_ids = [partner_ids[0] if len(set(partner_ids)) == 1 else 0]

        categ_ids = {}
        for p in products:
            categ = p.categ_id
            while categ:
                categ_ids[categ.id] = True
                categ = categ.parent_id

        categ_ids = list(categ_ids)

        is_product_template = products[0]._name == "product.template"
        if is_product_template:
            prod_tmpl_ids = [tmpl.id for tmpl in products]
            # all variants of all products
            prod_ids = [
                p.id
                for p in list(
                    chain.from_iterable([t.product_variant_ids for t in products])
                )
            ]
        else:
            prod_ids = [product.id for product in products]
            prod_tmpl_ids = [product.product_tmpl_id.id for product in products]

        # Load all rules
        self._cr.execute(
            "SELECT string_agg(item.id::text, ',') AS ids "
            "FROM product_pricelist_item AS item "
            "  LEFT JOIN product_category AS categ ON item.categ_id = categ.id "
            "WHERE (item.product_tmpl_id IS NULL OR item.product_tmpl_id = any(%s))"
            "  AND (item.product_id IS NULL OR item.product_id = any(%s))"
            "  AND (item.categ_id IS NULL OR item.categ_id = any(%s)) "
            "  AND (item.pricelist_id = %s) "
            "  AND (item.partner_id IS NULL OR item.partner_id = any(%s)) "
            "  AND (item.date_start IS NULL OR item.date_start<=%s) "
            "  AND (item.date_end IS NULL OR item.date_end>=%s)",
            (prod_tmpl_ids, prod_ids, categ_ids, self.id, partner_ids, date, date),
        )

        fetched_data = self._cr.fetchall()
        if fetched_data and fetched_data[0][0]:
            list_ids = eval(fetched_data[0][0])
            if isinstance(list_ids, int):
                list_ids = (list_ids,)
            items = self.env["product.pricelist.item"].search(
                [("id", "in", list_ids)],
                order="applied_on, partner_id, product_id, product_tmpl_id, date_start, min_quantity desc, categ_id desc, id desc",
            )
        else:
            items = self.env["product.pricelist.item"]

        results = {}

        for product, qty, partner in products_qty_partner:
            results[product.id] = 0.0
            suitable_rule = False

            # Final unit price is computed according to `qty` in the `qty_uom_id` UoM.
            # An intermediary unit price may be computed according to a different UoM, in
            # which case the price_uom_id contains that UoM.
            # The final price will be converted to match `qty_uom_id`.
            qty_uom_id = self._context.get("uom") or product.uom_id.id
            qty_in_product_uom = qty
            if qty_uom_id != product.uom_id.id:
                try:
                    qty_in_product_uom = (
                        self.env["uom.uom"]
                        .browse([self._context["uom"]])
                        ._compute_quantity(qty, product.uom_id)
                    )
                except UserError:
                    # Ignored - incompatible UoM in context, use default product UoM
                    pass

            # if Public user try to access standard price from website sale, need to call price_compute.
            # TDE SURPRISE: product can actually be a template
            price = product.price_compute("list_price")[product.id]
            price_uom = self.env["uom.uom"].browse([qty_uom_id])

            # Adicionado pela Multidados:
            # Se o parceiro for definido, procura a regra de preço específica para
            # o parceiro, caso contrário, procura a regra de preço padrão.
            partner_rule = False

            for rule in items:
                if rule.applied_on == '0_product_variant' and partner_rule:
                    continue

                # Recupera valores do contexto para evitar múltiplas chamadas ao método `get`
                old_price = self.env.context.get("old_price", 0)
                uom_qty_change = self.env.context.get("uom_qty_change", False)

                # Se a quantidade mínima não for definida e o contexto indicar alteração de quantidade
                # de unidade de medida (uom_qty_change), e o preço antigo for maior que zero,
                # mantém o preço antigo e continua para a próxima regra.
                if not rule.min_quantity and uom_qty_change and old_price > 0:
                    # Quando a tag 'uom_qty_change' está presente no contexto (indicando
                    # que o método foi acionado devido a um onchange no campo de quantidade)
                    # e a quantidade mínima do produto na lista de preços é zero, o preço
                    # unitário original é mantido. Isso evita que o preço unitário inserido
                    # manualmente pelo usuário seja sobrescrito pelo valor da lista de preços
                    # ao alterar a quantidade do produto em um orçamento.
                    price = old_price
                    continue

                if rule.min_quantity and qty_in_product_uom < rule.min_quantity:
                    continue

                if is_product_template:
                    if rule.product_tmpl_id and product.id != rule.product_tmpl_id.id:
                        continue

                    # product rule acceptable on template if has only one variant
                    if rule.product_id and not (
                        product.product_variant_count == 1
                        and product.product_variant_id.id == rule.product_id.id
                    ):
                        continue
                else:
                    if (
                        rule.product_tmpl_id
                        and product.product_tmpl_id.id != rule.product_tmpl_id.id
                    ):
                        continue

                    if rule.product_id and product.id != rule.product_id.id:
                        continue

                if rule.categ_id:
                    cat = product.categ_id
                    while cat:
                        if cat.id == rule.categ_id.id:
                            break
                        cat = cat.parent_id

                    if not cat:
                        continue

                if rule.base == "pricelist" and rule.base_pricelist_id:
                    price_tmp = rule.base_pricelist_id._compute_price_rule(
                        [(product, qty, partner)], date, uom_id
                    )[product.id][
                        0
                    ]  # TDE: 0 = price, 1 = rule
                    price = rule.base_pricelist_id.currency_id._convert(
                        price_tmp,
                        self.currency_id,
                        self.env.user.company_id,
                        date,
                        round=False,
                    )
                else:
                    # if base option is public price take sale price else cost price of product
                    # price_compute returns the price in the context UoM, i.e. qty_uom_id
                    price = product.price_compute(rule.base)[product.id]
                    if rule.applied_on == '0_product_variant' and rule.partner_id.id in partner_ids:
                        partner_rule = rule

                if price is not False:
                    price = rule._compute_price(
                        price, price_uom, product, quantity=qty, partner=partner
                    )
                    suitable_rule = rule

                break

            # Final price conversion into pricelist currency
            if (
                suitable_rule
                and suitable_rule.compute_price != "fixed"
                and suitable_rule.base != "pricelist"
            ):
                if suitable_rule.base == "standard_price":
                    cur = product.cost_currency_id
                else:
                    cur = product.currency_id

                price = cur._convert(
                    price, self.currency_id, self.env.user.company_id, date, round=False
                )

            results[product.id] = (
                price,
                suitable_rule and suitable_rule.id or False
            )

        return results

    # New methods: product based
    def get_products_price(self, products, quantities, partners, date=False, uom_id=False):
        """ For a given pricelist, return price for products
        Returns: dict{product_id: product price}, in the given pricelist """
        self.ensure_one()
        return {
            product_id: res_tuple[0]
            for product_id, res_tuple in self._compute_price_rule(
                list(pycompat.izip(products, quantities, partners)),
                date=date,
                uom_id=uom_id
            ).items()
        }

    def get_product_price(self, product, quantity, partner, date=False, uom_id=False):
        """ For a given pricelist, return price for a given product """
        self.ensure_one()
        return self._compute_price_rule([(product, quantity, partner)], date=date, uom_id=uom_id)[product.id][0]

    def get_product_price_rule(self, product, quantity, partner, date=False, uom_id=False):
        """ For a given pricelist, return price and rule for a given product """
        self.ensure_one()
        return self._compute_price_rule([(product, quantity, partner)], date=date, uom_id=uom_id)[product.id]

    # Compatibility to remove after v10 - DEPRECATED
    @api.model
    def _price_rule_get_multi(self, pricelist, products_by_qty_by_partner):
        """ Low level method computing the result tuple for a given pricelist and multi products - return tuple """
        return pricelist._compute_price_rule(products_by_qty_by_partner)

    @api.multi
    def price_get(self, prod_id, qty, partner=None):
        """ Multi pricelist, mono product - returns price per pricelist """
        return {key: price[0] for key, price in self.price_rule_get(prod_id, qty, partner=partner).items()}

    @api.multi
    def price_rule_get_multi(self, products_by_qty_by_partner):
        """ Multi pricelist, multi product  - return tuple """
        return self._compute_price_rule_multi(products_by_qty_by_partner)

    @api.multi
    def price_rule_get(self, prod_id, qty, partner=None):
        """ Multi pricelist, mono product - return tuple """
        product = self.env['product.product'].browse([prod_id])
        return self._compute_price_rule_multi([(product, qty, partner)])[prod_id]

    @api.model
    def _price_get_multi(self, pricelist, products_by_qty_by_partner):
        """ Mono pricelist, multi product - return price per product """
        return pricelist.get_products_price(
            list(pycompat.izip(**products_by_qty_by_partner)))

    # DEPRECATED (Not used anymore, see d39d583b2) -> Remove me in master (saas12.3)
    def _get_partner_pricelist(self, partner_id, company_id=None):
        """ Retrieve the applicable pricelist for a given partner in a given company.

            :param company_id: if passed, used for looking up properties,
             instead of current user's company
        """
        res = self._get_partner_pricelist_multi([partner_id], company_id)
        return res[partner_id].id

    def _get_partner_pricelist_multi_search_domain_hook(self):
        return []

    def _get_partner_pricelist_multi_filter_hook(self):
        return self

    def _get_partner_pricelist_multi(self, partner_ids, company_id=None):
        """ Retrieve the applicable pricelist for given partners in a given company.

            It will return the first found pricelist in this order:
            First, the pricelist of the specific property (res_id set), this one
                   is created when saving a pricelist on the partner form view.
            Else, it will return the pricelist of the partner country group
            Else, it will return the generic property (res_id not set), this one
                  is created on the company creation.
            Else, it will return the first available pricelist

            :param company_id: if passed, used for looking up properties,
                instead of current user's company
            :return: a dict {partner_id: pricelist}
        """
        # `partner_ids` might be ID from inactive uers. We should use active_test
        # as we will do a search() later (real case for website public user).
        Partner = self.env['res.partner'].with_context(active_test=False)

        Property = self.env['ir.property'].with_context(force_company=company_id or self.env.user.company_id.id)
        Pricelist = self.env['product.pricelist']
        pl_domain = self._get_partner_pricelist_multi_search_domain_hook()

        # if no specific property, try to find a fitting pricelist
        result = Property.get_multi('property_product_pricelist', Partner._name, partner_ids)

        remaining_partner_ids = [pid for pid, val in result.items() if not val or
                                 not val._get_partner_pricelist_multi_filter_hook()]
        if remaining_partner_ids:
            # get fallback pricelist when no pricelist for a given country
            pl_fallback = (
                Pricelist.search(pl_domain + [('country_group_ids', '=', False)], limit=1) or
                Property.get('property_product_pricelist', 'res.partner') or
                Pricelist.search(pl_domain, limit=1)
            )
            # group partners by country, and find a pricelist for each country
            domain = [('id', 'in', remaining_partner_ids)]
            groups = Partner.read_group(domain, ['country_id'], ['country_id'])
            for group in groups:
                country_id = group['country_id'] and group['country_id'][0]
                pl = Pricelist.search(pl_domain + [('country_group_ids.country_ids', '=', country_id)], limit=1)
                pl = pl or pl_fallback
                for pid in Partner.search(group['__domain']).ids:
                    result[pid] = pl

        return result

    @api.model
    def get_import_templates(self):
        return [{
            'label': _('Import Template for Pricelists'),
            'template': '/product/static/xls/product_pricelist.xls'
        }]


class ResCountryGroup(models.Model):
    _inherit = 'res.country.group'

    pricelist_ids = fields.Many2many('product.pricelist', 'res_country_group_pricelist_rel',
                                     'res_country_group_id', 'pricelist_id', string='Pricelists')


class PricelistItem(models.Model):
    _name = "product.pricelist.item"
    _description = "Pricelist Item"
    _order = "applied_on, min_quantity desc, categ_id desc, id desc"
    # NOTE: if you change _order on this model, make sure it matches the SQL
    # query built in _compute_price_rule() above in this file to avoid
    # inconstencies and undeterministic issues.

    product_tmpl_id = fields.Many2one(
        'product.template', 'Product Template', ondelete='cascade', index=True,
        help="Specify a template if this rule only applies to one product template. Keep empty otherwise.")
    product_id = fields.Many2one(
        'product.product', 'Product', ondelete='cascade', index=True,
        help="Specify a product if this rule only applies to one product. Keep empty otherwise.")
    categ_id = fields.Many2one(
        'product.category', 'Product Category', ondelete='cascade', index=True,
        help="Specify a product category if this rule only applies to products belonging to this category or its children categories. Keep empty otherwise.")

    # Adicionado pela Multidados
    # Para definir o preço específico para um parceiro
    partner_id = fields.Many2one(
        "res.partner",
        string="Partner",
        ondelete="cascade",
        index=True,
    )

    min_quantity = fields.Integer(
        'Min. Quantity', default=0,
        help="For the rule to apply, bought/sold quantity must be greater "
             "than or equal to the minimum quantity specified in this field.\n"
             "Expressed in the default unit of measure of the product.")
    applied_on = fields.Selection([
        ('3_global', 'Global'),
        ('2_product_category', ' Product Category'),
        ('1_product', 'Product'),
        ('0_product_variant', 'Product Variant')], "Apply On",
        default='3_global', required=True,
        help='Pricelist Item applicable on selected option')
    base = fields.Selection([
        ('list_price', 'Public Price'),
        ('standard_price', 'Cost'),
        ('pricelist', 'Other Pricelist')], "Based on",
        default='list_price', required=True,
        help='Base price for computation.\n'
             'Public Price: The base price will be the Sale/public Price.\n'
             'Cost Price : The base price will be the cost price.\n'
             'Other Pricelist : Computation of the base price based on another Pricelist.')
    base_pricelist_id = fields.Many2one('product.pricelist', 'Other Pricelist')
    pricelist_id = fields.Many2one('product.pricelist', 'Pricelist', index=True, ondelete='cascade')
    price_surcharge = fields.Float(
        'Price Surcharge', digits=dp.get_precision('Product Price'),
        help='Specify the fixed amount to add or substract(if negative) to the amount calculated with the discount.')
    price_discount = fields.Float('Price Discount', default=0, digits=(16, 2))
    price_round = fields.Float(
        'Price Rounding', digits=dp.get_precision('Product Price'),
        help="Sets the price so that it is a multiple of this value.\n"
             "Rounding is applied after the discount and before the surcharge.\n"
             "To have prices that end in 9.99, set rounding 10, surcharge -0.01")
    price_min_margin = fields.Float(
        'Min. Price Margin', digits=dp.get_precision('Product Price'),
        help='Specify the minimum amount of margin over the base price.')
    price_max_margin = fields.Float(
        'Max. Price Margin', digits=dp.get_precision('Product Price'),
        help='Specify the maximum amount of margin over the base price.')
    company_id = fields.Many2one(
        'res.company', 'Company',
        readonly=True, related='pricelist_id.company_id', store=True)
    currency_id = fields.Many2one(
        'res.currency', 'Currency',
        readonly=True, related='pricelist_id.currency_id', store=True)
    date_start = fields.Date('Start Date', help="Starting date for the pricelist item validation", index=True)
    date_end = fields.Date('End Date', help="Ending valid for the pricelist item validation", index=True)
    compute_price = fields.Selection([
        ('fixed', 'Fix Price'),
        ('percentage', 'Percentage (discount)'),
        ('formula', 'Formula')], index=True, default='fixed')
    fixed_price = fields.Float('Fixed Price', digits=dp.get_precision('Product Price'))
    percent_price = fields.Float('Percentage Price')
    # functional fields used for usability purposes
    name = fields.Char(
        'Name', compute='_get_pricelist_item_name_price',
        help="Explicit rule name for this pricelist line.")
    price = fields.Char(
        'Price', compute='_get_pricelist_item_name_price',
        help="Explicit rule name for this pricelist line.")
    with_partner = fields.Boolean(
        'With Partner',
        default=lambda self: self._context.get('with_partner', False)
    )

    @api.constrains('base_pricelist_id', 'pricelist_id', 'base')
    def _check_recursion(self):
        if any(item.base == 'pricelist' and item.pricelist_id and item.pricelist_id == item.base_pricelist_id for item in self):
            raise ValidationError(_('You cannot assign the Main Pricelist as Other Pricelist in PriceList Item'))
        return True

    @api.constrains('price_min_margin', 'price_max_margin')
    def _check_margin(self):
        if any(item.price_min_margin > item.price_max_margin for item in self):
            raise ValidationError(_('The minimum margin should be lower than the maximum margin.'))
        return True

    @api.one
    @api.depends('categ_id', 'product_tmpl_id', 'product_id', 'compute_price', 'fixed_price', \
        'pricelist_id', 'percent_price', 'price_discount', 'price_surcharge')
    def _get_pricelist_item_name_price(self):
        if self.categ_id:
            self.name = _("Category: %s") % (self.categ_id.name)
        elif self.product_tmpl_id:
            self.name = self.product_tmpl_id.name
        elif self.product_id:
            self.name = self.product_id.display_name.replace('[%s]' % self.product_id.code, '')
        else:
            self.name = _("All Products")

        if self.compute_price == 'fixed':
            decimal_places = self.env['decimal.precision'].precision_get('Product Price')
            self.price = ("%s %s") % (
                float_repr(
                    self.fixed_price,
                    decimal_places,
                ),
                self.pricelist_id.currency_id.name
            )
        elif self.compute_price == 'percentage':
            self.price = _("%s %% discount") % (self.percent_price)
        else:
            self.price = _("%s %% discount and %s surcharge") % (self.price_discount, self.price_surcharge)

    @api.onchange('applied_on')
    def _onchange_applied_on(self):
        if self.applied_on != '0_product_variant':
            self.product_id = False
        if self.applied_on != '1_product':
            self.product_tmpl_id = False
        if self.applied_on != '2_product_category':
            self.categ_id = False

    @api.onchange('compute_price')
    def _onchange_compute_price(self):
        if self.compute_price != 'fixed':
            self.fixed_price = 0.0
        if self.compute_price != 'percentage':
            self.percent_price = 0.0
        if self.compute_price != 'formula':
            self.update({
                'base': 'list_price',
                'price_discount': 0.0,
                'price_surcharge': 0.0,
                'price_round': 0.0,
                'price_min_margin': 0.0,
                'price_max_margin': 0.0,
            })

    @api.multi
    def write(self, values):
        res = super(PricelistItem, self).write(values)
        # When the pricelist changes we need the product.template price
        # to be invalided and recomputed.
        self.invalidate_cache()
        return res

    def _compute_price(self, price, price_uom, product, quantity=1.0, partner=False):
        """Compute the unit price of a product in the context of a pricelist application.
           The unused parameters are there to make the full context available for overrides.
        """
        self.ensure_one()
        convert_to_price_uom = (lambda price: product.uom_id._compute_price(price, price_uom))
        if self.compute_price == 'fixed':
            price = convert_to_price_uom(self.fixed_price)
        elif self.compute_price == 'percentage':
            price = (price - (price * (self.percent_price / 100))) or 0.0
        else:
            # complete formula
            price_limit = price
            price = (price - (price * (self.price_discount / 100))) or 0.0
            if self.price_round:
                price = tools.float_round(price, precision_rounding=self.price_round)

            if self.price_surcharge:
                price_surcharge = convert_to_price_uom(self.price_surcharge)
                price += price_surcharge

            if self.price_min_margin:
                price_min_margin = convert_to_price_uom(self.price_min_margin)
                price = max(price, price_limit + price_min_margin)

            if self.price_max_margin:
                price_max_margin = convert_to_price_uom(self.price_max_margin)
                price = min(price, price_limit + price_max_margin)
        return price
