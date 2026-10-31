"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { getChannelDashboard } from "@/lib/channel-api";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";

export function ChannelSyncAlertBanner() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const enabled = perms.canManageHotel || perms.canGenerateReports;

  const q = useQuery({
    queryKey: ["hotel-channels", "sync-banner"],
    queryFn: getChannelDashboard,
    enabled,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  if (!enabled || !q.data) return null;

  const connectionErrors = (q.data.connections || []).filter(
    (c) => c.last_error || c.status === "error",
  );
  const logErrors = q.data.errors || [];
  if (connectionErrors.length === 0 && logErrors.length === 0) return null;

  const sample =
    connectionErrors[0]?.last_error ||
    logErrors[0]?.message ||
    "Channel sync needs attention";

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-950 dark:text-amber-100">
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <span className="font-medium">OTA sync issue</span>
        <span className="mx-1.5 text-muted-foreground">·</span>
        <span className="text-muted-foreground">
          {connectionErrors.length
            ? `${connectionErrors.length} channel(s) in error`
            : `${logErrors.length} recent sync error(s)`}
          {": "}
          {sample}
        </span>
      </div>
      <Link
        href="/app/hotel/settings/channels"
        className="shrink-0 font-medium underline underline-offset-2"
      >
        Open channel manager
      </Link>
    </div>
  );
}
