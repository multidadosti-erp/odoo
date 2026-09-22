from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestPasswordPolicy(TransactionCase):

    def setUp(self):
        super(TestPasswordPolicy, self).setUp()
        self.env['ir.config_parameter'].sudo().set_param(
            'auth_password_policy.minlength', 8,
        )
        self.user = self.env['res.users'].with_context(
            no_reset_password=True,
        ).create({
            'name': 'Password Policy Test',
            'login': 'password-policy-test',
            'password': 'valid-password',
        })

    def test_short_password_is_rejected(self):
        with self.assertRaises(UserError):
            self.user.password = 'short'

    def test_init_encrypts_legacy_short_password(self):
        self.env.cr.execute(
            'UPDATE res_users SET password = %s WHERE id = %s',
            ('short', self.user.id),
        )
        self.user.invalidate_cache(['password'])

        self.env['res.users'].init()

        self.env.cr.execute(
            'SELECT password FROM res_users WHERE id = %s',
            (self.user.id,),
        )
        password = self.env.cr.fetchone()[0]
        self.assertNotEqual(
            'plaintext', self.user._crypt_context().identify(password),
        )