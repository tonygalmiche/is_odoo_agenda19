# -*- coding: utf-8 -*-
{
    "name" : "InfoSaône - Module Odoo 19 pour Agenda Plastigray",
    "version" : "19.0.1.0.0",
    "author" : "InfoSaône / Tony Galmiche",
    "category" : "InfoSaône",
    "description": """
    InfoSaône - Module Odoo 19 pour Agenda Plastigray
    ===================================================
    InfoSaône - Module Odoo 19 pour Agenda Plastigray
    """,
    "maintainer": "InfoSaône",
    "website": "https://infosaone.com",
    "license": "AGPL-3",
    "depends" : [
        "base",
        "calendar",
        "google_calendar",
    ], 
    "data" : [
        "security/res.groups.xml",
        "security/ir.model.access.csv",
        "security/ir.rule.xml",
        "views/calendar_views.xml",
        "views/res_users_views.xml",
        "views/mail_data.xml",
        "views/menu.xml",
    ],
    "installable": True,
    "application": True,
    "assets": {
        "web.assets_backend": [
            "is_odoo_agenda19/static/src/**/*",
        ],
    },
}
