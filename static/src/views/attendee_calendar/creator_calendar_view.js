/** @odoo-module **/
import { registry } from "@web/core/registry";
import { attendeeCalendarView } from "@calendar/views/attendee_calendar/attendee_calendar_view";
import { AttendeeCalendarModel } from "@calendar/views/attendee_calendar/attendee_calendar_model";

class CreatorCalendarModel extends AttendeeCalendarModel {
    /**
     * Surcharge : colorie chaque événement avec la couleur du créateur (user_id.partner_id.is_calendar_color)
     * au lieu de celle du participant connecté.
     */
    async updateAttendeeData(data) {
        await super.updateAttendeeData(...arguments);

        for (const record of Object.values(data.records)) {
            const creatorColor = record.rawRecord.is_creator_calendar_color;
            if (creatorColor) {
                record.colorIndex = creatorColor;
            }
        }
    }
}

export const creatorCalendarView = {
    ...attendeeCalendarView,
    Model: CreatorCalendarModel,
};

registry.category("views").add("creator_calendar", creatorCalendarView);
