# -*- coding: utf-8 -*-
{
    'name': "Check Printing in Expenses",
    'summary': """Print amount in words on checks issued for expenses""",
    'category': 'Accounting',
    'description': """
        Print amount in words on checks issued for expenses
    """,
    'version': '1.0',
    'depends': ['account_check_printing', 'hr_expense'],
    'installable': False,
    'auto_install': False,
    'data': [
        'views/payment.xml',
    ],
    'license': 'LGPL-3',
}
