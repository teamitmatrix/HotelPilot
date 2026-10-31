"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Legacy SuperAdmin integrations → /settings?tab=integrations */
export default function IntegrationsRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/settings?tab=integrations");
  }, [router]);

  return (
    <div className="flex min-h-[40vh] items-center justify-center text-sm text-muted-foreground">
      Redirecting to Integrations…
    </div>
  );
}
