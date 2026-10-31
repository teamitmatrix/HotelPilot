"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** Legacy SuperAdmin audit → /audit-logs */
export default function AuditRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/audit-logs");
  }, [router]);

  return (
    <div className="flex min-h-[40vh] items-center justify-center text-sm text-muted-foreground">
      Redirecting to Audit Logs…
    </div>
  );
}
