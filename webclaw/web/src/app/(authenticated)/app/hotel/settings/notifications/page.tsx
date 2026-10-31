"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Canonical notifications settings URL → tab on hotel settings. */
export default function HotelNotificationsSettingsRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/app/hotel/settings?tab=notifications");
  }, [router]);
  return null;
}
