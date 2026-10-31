"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useChat } from "@/lib/chat";
import { ChannelSyncAlertBanner } from "@/components/hotel/channel-sync-alert-banner";

/** Keep AI Assistant page context aligned with hotel ops routes. */
export default function HotelAppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname() || "";
  const { setPageContext } = useChat();

  useEffect(() => {
    const segment = pathname.split("/app/hotel/")[1]?.split("/")[0] || "dashboard";
    setPageContext({
      skill: "hospitalityclaw",
      view: "dashboard",
      entity: segment,
    });
  }, [pathname, setPageContext]);

  return (
    <>
      <ChannelSyncAlertBanner />
      {children}
    </>
  );
}
