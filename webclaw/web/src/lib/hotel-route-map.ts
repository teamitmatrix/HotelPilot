"use client";

/**
 * Map legacy /skills/hospitalityclaw/* URLs onto the hotel app routes.
 * Hotel ops no longer use the generic WebClaw skill chrome for PMS.
 */
export function hospitalityclawAppPath(pathname: string): string | null {
  if (!pathname.startsWith("/skills/hospitalityclaw")) return null;

  const rest = pathname.slice("/skills/hospitalityclaw".length).replace(/^\//, "");
  const segment = rest.split("/")[0] || "";

  if (!segment || segment === "actions") return "/app/hotel/dashboard";

  const map: Record<string, string> = {
    hotels: "/app/hotel/settings?tab=property",
    rooms: "/app/hotel/inventory",
    "room-types": "/app/hotel/inventory",
    "rate-plans": "/app/hotel/inventory",
    "tax-rules": "/app/hotel/inventory",
    reservations: "/app/hotel/desk",
    guests: "/app/hotel/desk",
    folios: "/app/hotel/billing",
    revenue: "/app/hotel/revenue",
  };

  return map[segment] || "/app/hotel/dashboard";
}
