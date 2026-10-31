"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Legacy path — inventory now lives beside billing and housekeeping. */
export default function HotelInventoryLegacyRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/app/hotel/inventory");
  }, [router]);
  return null;
}
