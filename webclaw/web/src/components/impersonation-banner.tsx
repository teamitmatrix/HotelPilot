"use client";

import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";

export function ImpersonationBanner() {
  const { user, stopImpersonation } = useAuth();
  if (!user?.is_impersonating) return null;

  return (
    <div className="flex items-center justify-between gap-3 border-b bg-amber-500/15 px-4 py-2 text-sm text-amber-950 dark:text-amber-100">
      <div className="min-w-0">
        <p>
          Viewing as{" "}
          <span className="font-semibold">{user.impersonated_hotel || "hotel"}</span>
          . Session expires in ~15 minutes. Platform admin is paused until you exit.
        </p>
        {user.impersonation_reason ? (
          <p className="truncate text-xs opacity-80">
            Reason: {user.impersonation_reason}
          </p>
        ) : null}
      </div>
      <Button size="sm" variant="outline" onClick={() => stopImpersonation()}>
        Exit impersonation
      </Button>
    </div>
  );
}
