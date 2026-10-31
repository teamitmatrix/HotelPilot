/** Country dial codes for hotel phone fields (E.164). */

export type DialCountry = {
  iso: string;
  name: string;
  dial: string; // without +
  flag: string;
};

/** Common hospitality markets first, then alphabetical. */
export const DIAL_COUNTRIES: DialCountry[] = [
  { iso: "PK", name: "Pakistan", dial: "92", flag: "🇵🇰" },
  { iso: "AE", name: "United Arab Emirates", dial: "971", flag: "🇦🇪" },
  { iso: "SA", name: "Saudi Arabia", dial: "966", flag: "🇸🇦" },
  { iso: "US", name: "United States", dial: "1", flag: "🇺🇸" },
  { iso: "GB", name: "United Kingdom", dial: "44", flag: "🇬🇧" },
  { iso: "IN", name: "India", dial: "91", flag: "🇮🇳" },
  { iso: "AF", name: "Afghanistan", dial: "93", flag: "🇦🇫" },
  { iso: "AU", name: "Australia", dial: "61", flag: "🇦🇺" },
  { iso: "BD", name: "Bangladesh", dial: "880", flag: "🇧🇩" },
  { iso: "BH", name: "Bahrain", dial: "973", flag: "🇧🇭" },
  { iso: "CA", name: "Canada", dial: "1", flag: "🇨🇦" },
  { iso: "CN", name: "China", dial: "86", flag: "🇨🇳" },
  { iso: "DE", name: "Germany", dial: "49", flag: "🇩🇪" },
  { iso: "EG", name: "Egypt", dial: "20", flag: "🇪🇬" },
  { iso: "FR", name: "France", dial: "33", flag: "🇫🇷" },
  { iso: "ID", name: "Indonesia", dial: "62", flag: "🇮🇩" },
  { iso: "IT", name: "Italy", dial: "39", flag: "🇮🇹" },
  { iso: "JO", name: "Jordan", dial: "962", flag: "🇯🇴" },
  { iso: "KW", name: "Kuwait", dial: "965", flag: "🇰🇼" },
  { iso: "LK", name: "Sri Lanka", dial: "94", flag: "🇱🇰" },
  { iso: "MY", name: "Malaysia", dial: "60", flag: "🇲🇾" },
  { iso: "NG", name: "Nigeria", dial: "234", flag: "🇳🇬" },
  { iso: "NL", name: "Netherlands", dial: "31", flag: "🇳🇱" },
  { iso: "NP", name: "Nepal", dial: "977", flag: "🇳🇵" },
  { iso: "OM", name: "Oman", dial: "968", flag: "🇴🇲" },
  { iso: "PH", name: "Philippines", dial: "63", flag: "🇵🇭" },
  { iso: "QA", name: "Qatar", dial: "974", flag: "🇶🇦" },
  { iso: "SG", name: "Singapore", dial: "65", flag: "🇸🇬" },
  { iso: "TR", name: "Turkey", dial: "90", flag: "🇹🇷" },
  { iso: "ZA", name: "South Africa", dial: "27", flag: "🇿🇦" },
];

const DEFAULT_ISO = "PK";

export function defaultDialCountry(): DialCountry {
  return DIAL_COUNTRIES.find((c) => c.iso === DEFAULT_ISO) || DIAL_COUNTRIES[0];
}

export function digitsOnly(value: string): string {
  return value.replace(/\D/g, "");
}

/** Strip leading 0 from national numbers (common in PK/UK local dialing). */
export function normalizeNationalNumber(national: string): string {
  const digits = digitsOnly(national);
  if (digits.length > 1 && digits.startsWith("0")) {
    return digits.replace(/^0+/, "");
  }
  return digits;
}

export function formatE164(dial: string, national: string): string {
  const n = normalizeNationalNumber(national);
  if (!n) return "";
  return `+${digitsOnly(dial)}${n}`;
}

export function parsePhoneValue(value: string | null | undefined): {
  country: DialCountry;
  national: string;
} {
  const raw = (value || "").trim();
  if (!raw) {
    return { country: defaultDialCountry(), national: "" };
  }

  const digits = digitsOnly(raw.startsWith("+") ? raw.slice(1) : raw);
  // Longest dial match first so +971 beats +9
  const sorted = [...DIAL_COUNTRIES].sort((a, b) => b.dial.length - a.dial.length);
  for (const country of sorted) {
    if (digits.startsWith(country.dial) && digits.length > country.dial.length) {
      return {
        country,
        national: digits.slice(country.dial.length),
      };
    }
  }

  // Local number without country — keep digits, default country
  return { country: defaultDialCountry(), national: digits };
}
