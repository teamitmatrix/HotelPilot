"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useActivePropertyId } from "@/lib/active-tenant";
import { resolveChatSessionScope } from "@/lib/chat-scope";
import { useChat } from "@/lib/chat";

/**
 * Keeps AI Assistant session scope in sync with auth, active property, and route.
 * Mount once under ChatProvider (authenticated layout).
 */
export function ChatScopeSync() {
  const { user } = useAuth();
  const pathname = usePathname() || "";
  const activePropertyId = useActivePropertyId();
  const { setSessionScope } = useChat();

  useEffect(() => {
    setSessionScope(resolveChatSessionScope(user, pathname, activePropertyId));
  }, [user, pathname, activePropertyId, setSessionScope]);

  return null;
}
