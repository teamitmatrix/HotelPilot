"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { isSuperAdmin, useAuth } from "@/lib/auth";
import { postLoginPath } from "@/lib/auth-routing";
import { OperatorDashboard } from "../../../dashboard/page";

export default function HotelDashboardPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const blocked = Boolean(user && isSuperAdmin(user));

  useEffect(() => {
    if (!loading && user && blocked) {
      router.replace(postLoginPath(user));
    }
  }, [blocked, loading, router, user]);

  if (loading || !user || blocked) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  return <OperatorDashboard />;
}
