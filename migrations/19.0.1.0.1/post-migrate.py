from odoo import api, SUPERUSER_ID
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Restreint le menu Apps aux administrateurs système uniquement.

    Exécuté uniquement lors d'une MISE À JOUR (--update), quand la version
    en base est inférieure à 19.0.1.0.1.
    Pour l'installation initiale, c'est hooks.py (post_init_hook) qui s'exécute.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    menu = env.ref('base.menu_management', raise_if_not_found=False)
    group = env.ref('base.group_system', raise_if_not_found=False)
    _logger.info("migrate is_odoo_agenda19: menu=%s group=%s", menu, group)
    if menu and group:
        menu.write({'group_ids': [(6, 0, [group.id])]})
        _logger.info("migrate is_odoo_agenda19: menu Apps restreint au groupe %s (id=%s)", group.name, group.id)
    else:
        _logger.warning("migrate is_odoo_agenda19: menu ou groupe introuvable !")
