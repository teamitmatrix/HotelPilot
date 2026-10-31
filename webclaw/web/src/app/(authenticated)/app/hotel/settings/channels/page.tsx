"use client";

import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { ChannelManagerPanel } from "@/components/hotel/channel-manager-panel";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";

export default function HotelChannelsPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mr-2 h-4" />
        <h1 className="text-sm font-medium">Channel manager</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>
      <div className="flex-1 space-y-4 p-6">
        {perms.canManageHotel ? (
          <ChannelManagerPanel />
        ) : (
          <p className="text-sm text-muted-foreground">
            Only hotel owners and admins can manage OTA channels.
          </p>
        )}
      </div>
    </div>
  );
}
