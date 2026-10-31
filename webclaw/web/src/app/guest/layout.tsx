import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Guest portal",
  description: "Contactless pre-check-in, folio, and checkout",
  robots: { index: false, follow: false },
};

/** Public guest self-service — no authenticated shell. */
export default function GuestPortalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
