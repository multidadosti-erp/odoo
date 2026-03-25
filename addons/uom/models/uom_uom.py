# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import re
import unicodedata

from odoo import api, fields, tools, models, _
from odoo.exceptions import UserError, ValidationError


class UoMCategory(models.Model):
    _name = 'uom.category'
    _description = 'Product UoM Categories'

    name = fields.Char('Unit of Measure Category', required=True, translate=True)
    measure_type = fields.Selection([
        ('unit', 'Units'),
        ('weight', 'Weight'),
        ('time', 'Time'),
        ('length', 'Length'),
        ('volume', 'Volume'),
    ], string="Type of Measure")

    _sql_constraints = [
        ('uom_category_unique_type', 'UNIQUE(measure_type)', 'You can have only one category per measurement type.'),
    ]

    @api.multi
    def unlink(self):
        if self.filtered(lambda categ: categ.measure_type == 'time'):
            raise UserError(_("You cannot delete this UoM Category as it is used by the system."))
        return super(UoMCategory, self).unlink()


class UoM(models.Model):
    _name = 'uom.uom'
    _description = 'Product Unit of Measure'
    _order = "name"

    _FISCAL_NAME_MAP = {
        'UN': 'UN',
        'UNIT': 'UN',
        'UNITS': 'UN',
        'UNIDADE': 'UN',
        'UNIDADES': 'UN',
        'KG': 'KG',
        'KILOGRAM': 'KG',
        'KILOGRAMS': 'KG',
        'QUILOGRAMA': 'KG',
        'QUILOGRAMAS': 'KG',
        'G': 'G',
        'GRAM': 'G',
        'GRAMS': 'G',
        'GRAMA': 'G',
        'GRAMAS': 'G',
        'LT': 'LT',
        'LITER': 'LT',
        'LITERS': 'LT',
        'LITRO': 'LT',
        'LITROS': 'LT',
        'M': 'M',
        'METRO': 'M',
        'M2': 'M2',
        'METROQUADRADO': 'M2',
        'METROSQUADRADOS': 'M2',
        'M3': 'M3',
        'METROCUBICO': 'M3',
        'METROSCUBICOS': 'M3',
        'TON': 'TON',
        'TONELADA': 'TON',
        'TONELADAS': 'TON',
        'T': 'TON',
        'DZ': 'DZ',
        'DUZIA': 'DZ',
        'DUZIAS': 'DZ',
        'DOZEN': 'DZ',
        'DOZENS': 'DZ',
        'PR': 'PR',
        'PAR': 'PR',
        'PARES': 'PR',
        'PAIR': 'PR',
        'PAIRS': 'PR',
        '1000UN': '1000UN',
        'MILHEIRO': '1000UN',
        'MWHORA': 'MWHORA',
        'MWH': 'MWHORA',
        'MEGAWATTHORA': 'MWHORA',
        'MEGAWATTHOUR': 'MWHORA',
        'QUILAT': 'QUILAT',
        'QUILATE': 'QUILAT',
        'KARAT': 'QUILAT',
        'JOGO': 'JOGO',
        'JOGOS': 'JOGO',
        'SET': 'JOGO',
        'SETS': 'JOGO',
    }

    name = fields.Char('Unit of Measure', required=True, translate=True)
    fiscal_name = fields.Char(
        'Fiscal Name',
        help='Official fiscal/statistical UoM code used for Foreign Trade in NF-e uTrib (e.g., UN, KG, LT).',
    )
    category_id = fields.Many2one(
        'uom.category', 'Category', required=True, ondelete='cascade',
        help="Conversion between Units of Measure can only occur if they belong to the same category. The conversion will be made based on the ratios.")
    factor = fields.Float(
        'Ratio', default=1.0, digits=0, required=True,  # force NUMERIC with unlimited precision
        help='How much bigger or smaller this unit is compared to the reference Unit of Measure for this category: 1 * (reference unit) = ratio * (this unit)')
    factor_inv = fields.Float(
        'Bigger Ratio', compute='_compute_factor_inv', digits=0,  # force NUMERIC with unlimited precision
        readonly=True, required=True,
        help='How many times this Unit of Measure is bigger than the reference Unit of Measure in this category: 1 * (this unit) = ratio * (reference unit)')
    rounding = fields.Float(
        'Rounding Precision', default=0.01, digits=0, required=True,
        help="The computed quantity will be a multiple of this value. "
             "Use 1.0 for a Unit of Measure that cannot be further split, such as a piece.")
    active = fields.Boolean('Active', default=True, help="Uncheck the active field to disable a unit of measure without deleting it.")
    uom_type = fields.Selection([
        ('bigger', 'Bigger than the reference Unit of Measure'),
        ('reference', 'Reference Unit of Measure for this category'),
        ('smaller', 'Smaller than the reference Unit of Measure')], 'Type',
        default='reference', required=1)
    measure_type = fields.Selection(string="Type of measurement category", related='category_id.measure_type', store=True, readonly=True)

    _sql_constraints = [
        ('factor_gt_zero', 'CHECK (factor!=0)', 'The conversion ratio for a unit of measure cannot be 0!'),
        ('rounding_gt_zero', 'CHECK (rounding>0)', 'The rounding precision must be strictly positive.'),
        ('factor_reference_is_one', "CHECK((uom_type = 'reference' AND factor = 1.0) OR (uom_type != 'reference'))", "The reference unit must have a conversion factor equal to 1.")
    ]

    @api.one
    @api.depends('factor')
    def _compute_factor_inv(self):
        self.factor_inv = self.factor and (1.0 / self.factor) or 0.0

    @api.model
    def _normalize_uom_label(self, label):
        normalized = unicodedata.normalize('NFKD', (label or '').strip().upper())
        normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
        return re.sub(r'[^A-Z0-9]+', '', normalized)

    @api.model
    def _guess_fiscal_name(self, label):
        key = self._normalize_uom_label(label)
        return self._FISCAL_NAME_MAP.get(key, False)

    @api.model
    def _populate_missing_fiscal_names(self):
        for uom in self.search([('fiscal_name', '=', False)]):
            uom.fiscal_name = self._guess_fiscal_name(uom.name) or uom.name

    @api.model
    def _register_hook(self):
        result = super(UoM, self)._register_hook()
        self._populate_missing_fiscal_names()
        return result

    @api.onchange('uom_type')
    def _onchange_uom_type(self):
        if self.uom_type == 'reference':
            self.factor = 1

    @api.constrains('category_id', 'uom_type', 'active')
    def _check_category_reference_uniqueness(self):
        """ Force the existence of only one UoM reference per category
            NOTE: this is a constraint on the all table. This might not be a good practice, but this is
            not possible to do it in SQL directly.
        """
        category_ids = self.mapped('category_id').ids
        self._cr.execute("""
            SELECT C.id AS category_id, count(U.id) AS uom_count
            FROM uom_category C
            LEFT JOIN uom_uom U ON C.id = U.category_id AND uom_type = 'reference' AND U.active = 't'
            WHERE C.id IN %s
            GROUP BY C.id
        """, (tuple(category_ids),))
        for uom_data in self._cr.dictfetchall():
            if uom_data['uom_count'] == 0:
                raise ValidationError(_("UoM category %s should have a reference unit of measure. If you just created a new category, please record the 'reference' unit first.") % (self.env['uom.category'].browse(uom_data['category_id']).name,))
            if uom_data['uom_count'] > 1:
                raise ValidationError(_("UoM category %s should only have one reference unit of measure.") % (self.env['uom.category'].browse(uom_data['category_id']).name,))

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if 'factor_inv' in values:
                factor_inv = values.pop('factor_inv')
                values['factor'] = factor_inv and (1.0 / factor_inv) or 0.0
            if values.get('name') and (not values.get('fiscal_name')):
                values['fiscal_name'] = self._guess_fiscal_name(values.get('name')) or values.get('name')
        return super(UoM, self).create(vals_list)

    @api.multi
    def write(self, values):
        if 'factor_inv' in values:
            factor_inv = values.pop('factor_inv')
            values['factor'] = factor_inv and (1.0 / factor_inv) or 0.0
        if values.get('name') and ('fiscal_name' not in values or not values.get('fiscal_name')):
            values['fiscal_name'] = self._guess_fiscal_name(values.get('name')) or values.get('name')
        result = super(UoM, self).write(values)
        if 'name' in values and 'fiscal_name' not in values:
            for uom in self.filtered(lambda record: not record.fiscal_name):
                uom.fiscal_name = uom._guess_fiscal_name(uom.name) or uom.name
        return result

    @api.multi
    def unlink(self):
        if self.filtered(lambda uom: uom.measure_type == 'time'):
            raise UserError(_("You cannot delete this UoM as it is used by the system. You should rather archive it."))
        return super(UoM, self).unlink()

    @api.model
    def name_create(self, name):
        """ The UoM category and factor are required, so we'll have to add temporary values
        for imported UoMs """
        values = {
            self._rec_name: name,
            'factor': 1
        }
        # look for the category based on the english name, i.e. no context on purpose!
        # TODO: should find a way to have it translated but not created until actually used
        if not self._context.get('default_category_id'):
            EnglishUoMCateg = self.env['uom.category'].with_context({})
            misc_category = EnglishUoMCateg.search([('name', '=', 'Unsorted/Imported Units')])
            if misc_category:
                values['category_id'] = misc_category.id
            else:
                values['category_id'] = EnglishUoMCateg.name_create('Unsorted/Imported Units')[0]
        new_uom = self.create(values)
        return new_uom.name_get()[0]

    @api.multi
    def _compute_quantity(self, qty, to_unit, round=True, rounding_method='UP', raise_if_failure=True):
        """ Convert the given quantity from the current UoM `self` into a given one
            :param qty: the quantity to convert
            :param to_unit: the destination UoM record (uom.uom)
            :param raise_if_failure: only if the conversion is not possible
                - if true, raise an exception if the conversion is not possible (different UoM category),
                - otherwise, return the initial quantity
        """
        if not self or not qty:
            return qty
        self.ensure_one()

        if self != to_unit and self.category_id.id != to_unit.category_id.id:
            if raise_if_failure:
                raise UserError(_('The unit of measure %s defined on the order line doesn\'t belong to the same category than the unit of measure %s defined on the product. Please correct the unit of measure defined on the order line or on the product, they should belong to the same category.') % (self.name, to_unit.name))
            else:
                return qty

        if self == to_unit:
            amount = qty
        else:
            amount = qty / self.factor
            if to_unit:
                amount = amount * to_unit.factor

        if to_unit and round:
            amount = tools.float_round(amount, precision_rounding=to_unit.rounding, rounding_method=rounding_method)

        return amount

    @api.multi
    def _compute_price(self, price, to_unit):
        self.ensure_one()
        if not self or not price or not to_unit or self == to_unit:
            return price
        if self.category_id.id != to_unit.category_id.id:
            return price
        amount = price * self.factor
        if to_unit:
            amount = amount / to_unit.factor
        return amount
