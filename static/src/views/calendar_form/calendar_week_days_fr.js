import { registry } from "@web/core/registry";
import { CalendarWeekDays } from "@calendar/views/widgets/calendar_week_days";

// Le widget CalendarWeekDays utilise fieldDependencies pour définir le string des jours.
// Le template affiche props.record.fields[day].string[0] (première lettre).
// Par défaut, les strings sont _t("Mon"), _t("Tue")... et sans traduction JS "Mon"→"Lundi",
// le widget reste en anglais (M T W T F S S) même pour un utilisateur fr_FR.
// On surcharge l'entrée du registre pour imposer les noms français : L M M J V S D.
registry.category("view_widgets").add("calendar_week_days", {
    component: CalendarWeekDays,
    fieldDependencies: [
        { name: "sun", type: "boolean", string: "Dimanche", readonly: false },
        { name: "mon", type: "boolean", string: "Lundi",    readonly: false },
        { name: "tue", type: "boolean", string: "Mardi",    readonly: false },
        { name: "wed", type: "boolean", string: "Mercredi", readonly: false },
        { name: "thu", type: "boolean", string: "Jeudi",    readonly: false },
        { name: "fri", type: "boolean", string: "Vendredi", readonly: false },
        { name: "sat", type: "boolean", string: "Samedi",   readonly: false },
    ],
}, { force: true });
