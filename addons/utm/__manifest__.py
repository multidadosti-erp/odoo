{
    "name": "UTM Trackers",
    "category": "Hidden",
    "description": """
Enable UTM trackers in shared links.
=====================================================
        """,
    "version": "1.0",
    "license": "LGPL-3",
    "depends": [
        "base",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/utm_data.xml",
        "views/utm_source.xml",
        "views/utm_medium.xml",
        "views/utm_campaign.xml",
        "views/menus.xml",
    ],
    "demo": [],
    "installable": True,
    "application": False,
    "auto_install": False,
}
