"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Staff management moved under Hotel settings. */
export default function HotelUsersLegacyRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/app/hotel/settings?tab=staff");
  }, [router]);
  return null;
}
