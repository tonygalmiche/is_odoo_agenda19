from odoo import api, SUPERUSER_ID
import logging

_logger = logging.getLogger(__name__)


def post_migrate(cr, registry):
    """Restreint le menu Apps aux administrateurs système uniquement.

    Exécuté uniquement lors de l'INSTALLATION INITIALE du module (post_init_hook).
    Pour la mise à jour (--update), c'est le script
    migrations/19.0.1.0.1/post-migrate.py qui s'exécute à la place.

    Note : cela ne peut pas être fait via XML (record dans menu.xml) car la
    fonction ref() n'est pas disponible dans le contexte d'évaluation des
    fichiers de données chargés lors d'un --update. Le hook post_init_hook
    s'exécute après le chargement de tous les modules et dispose d'un
    environnement complet, ce qui permet d'utiliser env.ref() sans erreur.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    menu = env.ref('base.menu_management', raise_if_not_found=False)
    group = env.ref('base.group_system', raise_if_not_found=False)
    _logger.info("post_migrate is_odoo_agenda19: menu=%s group=%s", menu, group)
    if menu and group:
        menu.write({'group_ids': [(6, 0, [group.id])]})
        _logger.info("post_migrate is_odoo_agenda19: menu Apps restreint au groupe %s (id=%s)", group.name, group.id)
    else:
        _logger.warning("post_migrate is_odoo_agenda19: menu ou groupe introuvable !")
