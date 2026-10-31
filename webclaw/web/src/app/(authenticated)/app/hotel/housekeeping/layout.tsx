import type { Viewport } from "next";

/** Lock zoom on housekeeping floor devices for consistent touch targets. */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function HousekeepingLayout({ children }: { children: React.ReactNode }) {
  return children;
}
