/** IANA time zones. Prefers the runtime zone database, with a full fallback. */

const FALLBACK_TIMEZONES = [
  "UTC",
  "Africa/Abidjan",
  "Africa/Accra",
  "Africa/Addis_Ababa",
  "Africa/Algiers",
  "Africa/Cairo",
  "Africa/Casablanca",
  "Africa/Johannesburg",
  "Africa/Lagos",
  "Africa/Nairobi",
  "Africa/Tunis",
  "America/Anchorage",
  "America/Argentina/Buenos_Aires",
  "America/Bogota",
  "America/Caracas",
  "America/Chicago",
  "America/Denver",
  "America/Edmonton",
  "America/Guatemala",
  "America/Halifax",
  "America/Jamaica",
  "America/Lima",
  "America/Los_Angeles",
  "America/Mexico_City",
  "America/New_York",
  "America/Panama",
  "America/Phoenix",
  "America/Puerto_Rico",
  "America/Santiago",
  "America/Sao_Paulo",
  "America/St_Johns",
  "America/Toronto",
  "America/Vancouver",
  "Asia/Almaty",
  "Asia/Amman",
  "Asia/Baghdad",
  "Asia/Baku",
  "Asia/Bangkok",
  "Asia/Beirut",
  "Asia/Colombo",
  "Asia/Dhaka",
  "Asia/Dubai",
  "Asia/Ho_Chi_Minh",
  "Asia/Hong_Kong",
  "Asia/Jakarta",
  "Asia/Jerusalem",
  "Asia/Karachi",
  "Asia/Kathmandu",
  "Asia/Kolkata",
  "Asia/Calcutta",
  "Asia/Kuala_Lumpur",
  "Asia/Kuwait",
  "Asia/Macau",
  "Asia/Manila",
  "Asia/Muscat",
  "Asia/Qatar",
  "Asia/Riyadh",
  "Asia/Seoul",
  "Asia/Shanghai",
  "Asia/Singapore",
  "Asia/Taipei",
  "Asia/Tashkent",
  "Asia/Tehran",
  "Asia/Tokyo",
  "Asia/Yangon",
  "Atlantic/Azores",
  "Atlantic/Reykjavik",
  "Australia/Adelaide",
  "Australia/Brisbane",
  "Australia/Darwin",
  "Australia/Melbourne",
  "Australia/Perth",
  "Australia/Sydney",
  "Europe/Amsterdam",
  "Europe/Athens",
  "Europe/Belgrade",
  "Europe/Berlin",
  "Europe/Brussels",
  "Europe/Bucharest",
  "Europe/Budapest",
  "Europe/Copenhagen",
  "Europe/Dublin",
  "Europe/Helsinki",
  "Europe/Istanbul",
  "Europe/Kyiv",
  "Europe/Lisbon",
  "Europe/London",
  "Europe/Madrid",
  "Europe/Moscow",
  "Europe/Oslo",
  "Europe/Paris",
  "Europe/Prague",
  "Europe/Rome",
  "Europe/Stockholm",
  "Europe/Vienna",
  "Europe/Warsaw",
  "Europe/Zurich",
  "Indian/Maldives",
  "Indian/Mauritius",
  "Pacific/Auckland",
  "Pacific/Fiji",
  "Pacific/Guam",
  "Pacific/Honolulu",
  "Pacific/Port_Moresby",
];

function ianaTimezones(): string[] {
  try {
    const supported = (
      Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
    ).supportedValuesOf;
    if (typeof supported === "function") {
      const zones = supported.call(Intl, "timeZone");
      if (Array.isArray(zones) && zones.length > 50) {
        const set = new Set([...zones, ...FALLBACK_TIMEZONES, "UTC"]);
        return [...set].sort((a, b) => a.localeCompare(b));
      }
    }
  } catch {
    /* use fallback */
  }
  return FALLBACK_TIMEZONES;
}

export const TIMEZONES = ianaTimezones();

export function timezoneLabel(tz: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      timeZoneName: "shortOffset",
    }).formatToParts(new Date());
    const offset = parts.find((p) => p.type === "timeZoneName")?.value;
    return offset ? `${tz.replace(/_/g, " ")} (${offset})` : tz.replace(/_/g, " ");
  } catch {
    return tz.replace(/_/g, " ");
  }
}

export function timezoneOptions(): { value: string; label: string }[] {
  return TIMEZONES.map((tz) => ({ value: tz, label: timezoneLabel(tz) }));
}

export function filterTimezones(query: string, zones: string[] = TIMEZONES): string[] {
  const q = query.trim().toLowerCase().replace(/\s+/g, "_");
  if (!q) return zones;
  return zones.filter((tz) => {
    const label = timezoneLabel(tz).toLowerCase();
    return tz.toLowerCase().includes(q) || label.includes(query.trim().toLowerCase());
  });
}
