"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function LegacyFoliosRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/app/hotel/billing");
  }, [router]);
  return (
    <div className="flex min-h-[40vh] items-center justify-center">
      <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
    </div>
  );
}
