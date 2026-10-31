"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Canonical payments settings URL → tab on hotel settings. */
export default function HotelPaymentsSettingsRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/app/hotel/settings?tab=payments");
  }, [router]);
  return null;
}
