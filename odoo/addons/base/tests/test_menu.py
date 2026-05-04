# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestMenu(TransactionCase):

    def test_00_menu_deletion(self):
        """Verify that menu deletion works properly when there are child menus, and those
           are indeed made orphans"""
        Menu = self.env['ir.ui.menu']
        root = Menu.create({'name': 'Test root'})
        child1 = Menu.create({'name': 'Test child 1', 'parent_id': root.id})
        child2 = Menu.create({'name': 'Test child 2', 'parent_id': root.id})
        child21 = Menu.create({'name': 'Test child 2-1', 'parent_id': child2.id})
        all_ids = [root.id, child1.id, child2.id, child21.id]

        # delete and check that direct children are promoted to top-level
        # cfr. explanation in menu.unlink()
        root.unlink()

        # Generic trick necessary for search() calls to avoid hidden menus 
        Menu = self.env['ir.ui.menu'].with_context({'ir.ui.menu.full_list': True})

        remaining = Menu.search([('id', 'in', all_ids)], order="id")
        self.assertEqual([child1.id, child2.id, child21.id], remaining.ids)

        orphans =  Menu.search([('id', 'in', all_ids), ('parent_id', '=', False)], order="id")
        self.assertEqual([child1.id, child2.id], orphans.ids)

    def test_01_separator_visibility(self):
        Menu = self.env['ir.ui.menu']
        action = self.env['ir.actions.act_window'].create({
            'name': 'Separator Visibility Action',
            'res_model': 'res.partner',
            'view_mode': 'tree,form',
        })

        root = Menu.create({'name': 'Root Menu'})
        folder = Menu.create({'name': 'Folder Menu', 'parent_id': root.id})
        separator = Menu.create({
            'name': 'Separator',
            'parent_id': folder.id,
            'web_icon': '__menu_separator__',
            'sequence': 15,
        })
        action_menu = Menu.create({
            'name': 'Action Menu',
            'parent_id': folder.id,
            'action': 'ir.actions.act_window,%d' % action.id,
            'sequence': 20,
        })
        top_separator = Menu.create({
            'name': 'Top Separator',
            'web_icon': '__menu_separator__',
        })

        visible_ids = set(Menu.search([
            ('id', 'in', [root.id, folder.id, separator.id, action_menu.id, top_separator.id])
        ]).ids)

        self.assertIn(separator.id, visible_ids)
        self.assertNotIn(top_separator.id, visible_ids)
