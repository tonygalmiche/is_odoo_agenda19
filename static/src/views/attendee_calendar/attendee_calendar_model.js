/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { AttendeeCalendarModel } from "@calendar/views/attendee_calendar/attendee_calendar_model";

patch(AttendeeCalendarModel.prototype, {
    /**
     * Surcharge pour utiliser is_calendar_color (défini sur res.partner)
     * comme colorIndex des événements et des filtres au lieu de l'ID du partenaire.
     * Si is_calendar_color = 0, on garde le comportement standard (ID partenaire).
     */
    async updateAttendeeData(data) {
        await super.updateAttendeeData(...arguments);

        // 1) Override colorIndex des événements
        for (const record of Object.values(data.records)) {
            if (record.attendeeId) {
                const attendeeInfo = data.attendees.find(
                    (a) => a.id === record.attendeeId && a.event_id === record.id
                );
                if (attendeeInfo && attendeeInfo.is_calendar_color) {
                    record.colorIndex = attendeeInfo.is_calendar_color;
                }
            }
        }

        // 2) Override colorIndex des filtres (sidebar)
        const attendeeFilters = data.filterSections.partner_ids;
        if (attendeeFilters) {
            for (const filter of attendeeFilters.filters) {
                if (filter.type !== "all" && filter.value) {
                    const attendeeInfo = data.attendees.find(
                        (a) => a.id === filter.value
                    );
                    if (attendeeInfo && attendeeInfo.is_calendar_color) {
                        filter.colorIndex = attendeeInfo.is_calendar_color;
                    }
                }
            }
        }
    },
});
