"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { isPlatformOperator, useAuth } from "@/lib/auth";
import { postLoginPath } from "@/lib/auth-routing";

/** Gate for platform admin modules (superadmin + platform.* staff). */
export default function PlatformAdminGate({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const allowed = isPlatformOperator(user);

  useEffect(() => {
    if (!loading && user && !allowed) {
      router.replace(postLoginPath(user));
    }
  }, [allowed, loading, router, user]);

  if (loading || !user || !allowed) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  return <>{children}</>;
}
