"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { SidebarProvider } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/app-sidebar";
import { ChatPanel } from "@/components/chat-panel";
import { ChatScopeSync } from "@/components/chat-scope-sync";
import { ChatProvider, useChat } from "@/lib/chat";
import { ToastProvider } from "@/components/toast-provider";
import { EventsProvider } from "@/lib/events";
import { ErrorBoundary } from "@/components/error-boundary";
import { QueryProvider } from "@/lib/query-provider";
import { useAuth } from "@/lib/auth";
import { ImpersonationBanner } from "@/components/impersonation-banner";
import { HotelContextBar } from "@/components/hotel/tenant-switcher";

// Inner component that can use useChat (must be inside ChatProvider)
function ChatKeyboardToggle() {
  const { toggle } = useChat();
  useEffect(() => {
    function handleToggle() {
      toggle();
    }
    window.addEventListener("ocui-chat-toggle", handleToggle);
    return () => window.removeEventListener("ocui-chat-toggle", handleToggle);
  }, [toggle]);
  return null;
}

export default function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [user, loading, router]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  if (!user) return null;

  return (
    <QueryProvider>
      <ToastProvider>
        <EventsProvider>
          <ChatProvider>
            <ChatKeyboardToggle />
            <ChatScopeSync />
            <SidebarProvider className="h-svh max-h-svh overflow-hidden">
              <AppSidebar />
              <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
                <HotelContextBar />
                <ImpersonationBanner />
                <main className="min-h-0 min-w-0 flex-1 overflow-auto bg-surface-bg">
                  <ErrorBoundary>{children}</ErrorBoundary>
                </main>
              </div>
              <ChatPanel />
            </SidebarProvider>
          </ChatProvider>
        </EventsProvider>
      </ToastProvider>
    </QueryProvider>
  );
}
