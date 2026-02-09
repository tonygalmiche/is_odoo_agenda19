# -*- coding: utf-8 -*-
from base64 import b64encode
from odoo import api, fields, models
from odoo.tools import html_escape

# Palette complète des 56 couleurs Odoo 19 (index 0 à 55)
# Correspondance avec $o-colors-complete dans secondary_variables.scss
CALENDAR_COLORS = [
    '#a2a2a2', '#ee2d2d', '#dc8534', '#e8bb1d', '#5794dd', '#9f628f',
    '#db8865', '#41a9a2', '#304be0', '#ee2f8a', '#61c36e', '#9872e6',
    '#aa4b6b', '#30C381', '#97743a', '#F7CD1F', '#4285F4', '#8E24AA',
    '#D6145F', '#173e43', '#348F50', '#AA3A38', '#795548', '#5e0231',
    '#6be585', '#999966', '#e9d362', '#b56969', '#bdc3c7', '#649173',
    '#ea00ff', '#ff0026', '#8bcc00', '#00bfaf', '#006aff', '#af00bf',
    '#bf001d', '#bf6300', '#8cff00', '#00f2ff', '#004ab3', '#ff00d0',
    '#ffa600', '#3acc00', '#00b6bf', '#0048ff', '#bf7c00', '#04ff00',
    '#00d0ff', '#0036bf', '#ff008c', '#00bf49', '#0092b3', '#0004ff',
    '#b20062', '#649173',
]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_calendar_color = fields.Integer('Couleur Agenda', default=0, help="Couleur utilisée dans la vue calendrier (0 à 55). 0 = couleur automatique basée sur l'ID.")
    is_calendar_color_table = fields.Html('Palette des couleurs', compute='_compute_calendar_color_table', sanitize=False)

    @api.depends('is_calendar_color')
    def _compute_calendar_color_table(self):
        for rec in self:
            selected = rec.is_calendar_color or 0
            cells = []
            for i, color in enumerate(CALENDAR_COLORS):
                h = color.lstrip('#')
                r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                # Reproduire mix($o-white, $color, 55%) du SCSS Odoo 19
                # = 55% blanc (255) + 45% couleur
                r_bg = int(0.55 * 255 + 0.45 * r)
                g_bg = int(0.55 * 255 + 0.45 * g)
                b_bg = int(0.55 * 255 + 0.45 * b)
                bg_color = f'#{r_bg:02x}{g_bg:02x}{b_bg:02x}'
                text_color = '#fff' if (0.299 * r_bg + 0.587 * g_bg + 0.114 * b_bg) < 128 else '#000'
                is_sel = (i == selected)
                border = f'3px solid {color}' if is_sel else '1px solid #ccc'
                font_w = 'bold' if is_sel else 'normal'
                shadow = f'box-shadow:0 0 6px 2px {color};transform:scale(1.15);z-index:1;' if is_sel else ''
                label = '0 (auto)' if i == 0 else str(i)
                cells.append(
                    f'<div style="display:inline-flex;align-items:center;justify-content:center;'
                    f'width:64px;height:36px;background-color:{bg_color};color:{text_color};'
                    f'border:{border};border-radius:4px;font-size:11px;font-weight:{font_w};'
                    f'margin:2px;position:relative;{shadow}">{label}</div>'
                )
            rec.is_calendar_color_table = (
                '<div style="display:flex;flex-wrap:wrap;gap:2px;max-width:750px;padding:8px;">'
                + ''.join(cells)
                + '</div>'
            )

    def get_attendee_detail(self, meeting_ids):
        """Surcharge pour ajouter is_calendar_color dans les détails des participants"""
        result = super().get_attendee_detail(meeting_ids)
        partner_colors = {p.id: p.is_calendar_color for p in self}
        for detail in result:
            detail['is_calendar_color'] = partner_colors.get(detail['id'], 0)
        return result

    @staticmethod
    def _mix_with_white(hex_color, white_pct=0.55):
        """Reproduit mix($o-white, $color, 55%) du SCSS Odoo 19"""
        h = hex_color.lstrip('#')
        r = int(white_pct * 255 + (1 - white_pct) * int(h[0:2], 16))
        g = int(white_pct * 255 + (1 - white_pct) * int(h[2:4], 16))
        b = int(white_pct * 255 + (1 - white_pct) * int(h[4:6], 16))
        return f'#{r:02x}{g:02x}{b:02x}'

    def _avatar_generate_svg(self):
        """Surcharge pour utiliser la couleur agenda (éclaircie) comme couleur d'avatar quand is_calendar_color > 0"""
        if self.is_calendar_color and 0 < self.is_calendar_color < len(CALENDAR_COLORS):
            initial = html_escape(self[self._avatar_name_field][0].upper())
            bgcolor = self._mix_with_white(CALENDAR_COLORS[self.is_calendar_color])
            # Couleur du texte : noir si fond clair, blanc si fond sombre
            h = bgcolor.lstrip('#')
            luma = 0.299 * int(h[0:2], 16) + 0.587 * int(h[2:4], 16) + 0.114 * int(h[4:6], 16)
            text_color = '#000000' if luma >= 128 else '#ffffff'
            return b64encode((
                "<?xml version='1.0' encoding='UTF-8' ?>"
                "<svg height='180' width='180' xmlns='http://www.w3.org/2000/svg' xmlns:xlink='http://www.w3.org/1999/xlink'>"
                f"<rect fill='{bgcolor}' height='180' width='180'/>"
                f"<text fill='{text_color}' font-size='96' text-anchor='middle' x='90' y='125' font-family='sans-serif'>{initial}</text>"
                "</svg>"
            ).encode())
        return super()._avatar_generate_svg()
