"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Legacy SuperAdmin settings → /settings?tab=platform */
export default function PlatformSettingsRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/settings?tab=platform");
  }, [router]);

  return (
    <div className="flex min-h-[40vh] items-center justify-center text-sm text-muted-foreground">
      Redirecting to System Settings…
    </div>
  );
}
