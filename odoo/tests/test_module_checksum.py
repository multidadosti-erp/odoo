import os
import shutil
import tempfile
import unittest

try:
    from unittest import mock
except ImportError:
    import mock

from odoo.modules import module
from odoo.modules import loading


class TestModuleChecksum(unittest.TestCase):

    def setUp(self):
        self.addon_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.addon_path)

    def _write(self, relative_path, content):
        path = os.path.join(self.addon_path, relative_path)
        directory = os.path.dirname(path)
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, 'wb') as stream:
            stream.write(content)

    def _checksum(self, **kwargs):
        with mock.patch.object(module, 'get_module_path',
                               return_value=self.addon_path):
            return module.get_module_checksum('test_addon', **kwargs)

    def test_checksum_changes_with_relevant_content(self):
        self._write('__manifest__.py', b"{'name': 'Test'}")
        initial = self._checksum()

        self._write('models/test.py', b'value = 1')

        self.assertNotEqual(initial, self._checksum())
        self.assertEqual(self._checksum(), self._checksum())

    def test_checksum_ignores_excluded_and_unused_translations(self):
        self._write('__manifest__.py', b"{'name': 'Test'}")
        initial = self._checksum(keep_langs=['pt_BR'])

        self._write('static/src/js/test.js', b'ignored')
        self._write('i18n/es.po', b'ignored')
        self._write('i18n/pt_PT.po', b'ignored')
        self._write('models/test.pyc', b'ignored')

        self.assertEqual(initial, self._checksum(keep_langs=['pt_BR']))

        self._write('i18n/pt.po', b'included fallback')
        self.assertNotEqual(initial, self._checksum(keep_langs=['pt_BR']))

        initial = self._checksum(keep_langs=['pt_BR'])
        self._write('i18n/pt_BR.po', b'included')
        self.assertNotEqual(initial, self._checksum(keep_langs=['pt_BR']))

    def test_save_reuses_current_checksum(self):
        parameter = mock.MagicMock()
        module_model = mock.MagicMock()
        module_record = mock.MagicMock()
        module_record.name = 'test_addon'
        module_model.search.return_value = [module_record]

        env = mock.MagicMock()
        env.__getitem__.side_effect = {
            'ir.config_parameter': parameter,
            'ir.module.module': module_model,
        }.__getitem__
        parameter.sudo.return_value = parameter

        with mock.patch.object(loading, '_get_saved_module_checksums',
                               return_value={}), \
                mock.patch.object(loading, '_get_checksum_options',
                                  return_value=((), [])), \
                mock.patch.object(loading, 'get_module_checksum') as checksum:
            loading._save_module_checksums(
                env, {'test_addon'}, {'test_addon': 'cached-checksum'},
            )

        checksum.assert_not_called()
        saved_value = parameter.set_param.call_args[0][1]
        self.assertIn('"test_addon": "cached-checksum"', saved_value)

    def test_changed_modules_are_resolved_without_registry_models(self):
        cursor = mock.MagicMock()
        cursor.fetchone.side_effect = [None, None]
        cursor.fetchall.side_effect = [
            [('pt_BR',)],
            [('base',), ('sale',)],
        ]

        with mock.patch.object(
                loading, 'get_module_checksum',
                side_effect=lambda name, patterns, langs: {
                    'base': 'base-current',
                    'sale': 'sale-current',
                }[name]):
            changed, current = loading._get_changed_modules(cursor)

        self.assertEqual(['base', 'sale'], changed)
        self.assertEqual({
            'base': 'base-current',
            'sale': 'sale-current',
        }, current)


if __name__ == '__main__':
    unittest.main()