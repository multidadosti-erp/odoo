# -*- encoding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, tools

class DecimalPrecision(models.Model):
    """Cadastro de precisao decimal por uso funcional."""

    _name = 'decimal.precision'
    _description = 'Decimal Precision'

    name = fields.Char('Usage', index=True, required=True)
    digits = fields.Integer('Digits', required=True, default=2)

    _sql_constraints = [
        ('name_uniq', 'unique (name)', """Only one value can be defined for each given usage!"""),
    ]

    @api.model
    def _normalize_application_name(self, application):
        """Normaliza a entrada de precisao para um valor simples.

        Este metodo aceita tanto formatos legados (string com nome do uso)
        quanto estruturas novas em dicionario usadas pelo frontend
        (default/rules/precision/value/decimal_precision).

        Regras importantes:
        - prioriza chaves diretas do dict (default, precision, value,
          decimal_precision);
        - depois tenta extrair de `rules`;
        - considera 0 como valor valido (nao depende de truthiness);
        - retorna False quando nao encontra nenhuma configuracao util.
        """
        if isinstance(application, dict):
            for key in ('default', 'precision', 'value', 'decimal_precision'):
                if key in application and application[key] is not None:
                    return application[key]

            rules = application.get('rules')
            if isinstance(rules, list):
                for rule in rules:
                    if not isinstance(rule, dict):
                        continue
                    for key in ('precision', 'value', 'decimal_precision'):
                        if key in rule and rule[key] is not None:
                            return rule[key]

            return False

        return application

    @api.model
    @tools.ormcache('application')
    def precision_get(self, application):
        """Retorna a quantidade de casas decimais para um uso/aplicacao.

        Fluxo:
        - normaliza o valor recebido;
        - se vier inteiro, usa o proprio inteiro como numero de casas;
        - se nao houver valor util, retorna 2 como padrao;
        - se vier nome de uso, busca no banco (`decimal_precision`).

        O resultado e cacheado por `application` via ormcache.
        """
        application = self._normalize_application_name(application)
        if isinstance(application, int):
            return application
        if application in (None, False, ''):
            return 2
        self.env.cr.execute('select digits from decimal_precision where name=%s', (application,))
        res = self.env.cr.fetchone()
        return res[0] if res else 2

    @api.model_cr
    def clear_cache(self):
        """Compatibilidade retroativa para limpar caches do modelo.

        Metodo legado mantido por compatibilidade. Internamente delega para
        `clear_caches`.
        """
        self.clear_caches()

    @api.model_create_multi
    def create(self, vals_list):
        """Cria registros de precisao e invalida cache apos gravacao."""
        res = super(DecimalPrecision, self).create(vals_list)
        self.clear_caches()
        return res

    @api.multi
    def write(self, data):
        """Atualiza registros de precisao e invalida cache apos gravacao."""
        res = super(DecimalPrecision, self).write(data)
        self.clear_caches()
        return res

    @api.multi
    def unlink(self):
        """Remove registros de precisao e invalida cache apos remocao."""
        res = super(DecimalPrecision, self).unlink()
        self.clear_caches()
        return res


class DecimalPrecisionFloat(models.AbstractModel):
    """Extensao do formatador QWeb de float com suporte a decimal_precision.

    Permite que templates QWeb informem `options.decimal_precision` e que esta
    configuracao tenha precedencia sobre o digits do campo.
    """
    _inherit = 'ir.qweb.field.float'


    @api.model
    def precision(self, field, options=None):
        """Resolve a precisao de renderizacao para campos float no QWeb.

        Uso:
        - le `options['decimal_precision']` quando informado;
        - normaliza o formato recebido (incluindo dict com regras/default);
        - resolve para numero de casas via `decimal.precision.precision_get`;
        - se nao houver opcao valida, usa comportamento padrao da superclasse.
        """
        dp = options and options.get('decimal_precision')
        if dp is not None and dp is not False:
            dp = self.env['decimal.precision']._normalize_application_name(dp)
            if dp is not None and dp is not False:
                return self.env['decimal.precision'].precision_get(dp)

        return super(DecimalPrecisionFloat, self).precision(field, options=options)

class DecimalPrecisionTestModel(models.Model):
    _name = 'decimal.precision.test'
    _description = 'Decimal Precision Test'

    float = fields.Float()
    float_2 = fields.Float(digits=(16, 2))
    float_4 = fields.Float(digits=(16, 4))
