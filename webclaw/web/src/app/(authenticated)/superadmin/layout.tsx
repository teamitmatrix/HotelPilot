"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { isPlatformOperator, useAuth } from "@/lib/auth";
import { postLoginPath } from "@/lib/auth-routing";
import { useChat } from "@/lib/chat";

export default function SuperAdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname() || "";
  const { setPageContext } = useChat();
  const allowed = isPlatformOperator(user);

  useEffect(() => {
    if (!loading && user && !allowed) {
      router.replace(postLoginPath(user));
    }
  }, [allowed, loading, router, user]);

  useEffect(() => {
    if (!allowed) return;
    const segment =
      pathname.split("/superadmin/")[1]?.split("/")[0] || "dashboard";
    setPageContext({
      view: "dashboard",
      entity: segment,
    });
  }, [allowed, pathname, setPageContext]);

  if (loading || !user || !allowed) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  return <>{children}</>;
}
