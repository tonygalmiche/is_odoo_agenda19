import { AttendeeCalendarController } from "@calendar/views/attendee_calendar/attendee_calendar_controller";
import { patch } from "@web/core/utils/patch";
import { user } from "@web/core/user";
import { onWillStart } from "@odoo/owl";

patch(AttendeeCalendarController.prototype, {
    setup() {
        super.setup(...arguments);
        onWillStart(async () => {

            console.log('TEST');


            this.isGoogleSyncGroupMember = await user.hasGroup(
                "is_odoo_agenda19.is_google_synchronisation_group"
            );
        });
    },
});
