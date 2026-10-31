"use client";

import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { postLoginPath } from "./auth-routing";

import { API_BASE } from "./api";
import { bindActiveTenant, getActiveTenantId, setActiveTenantId } from "./active-tenant";

export interface HotelMembership {
  tenant_id: string;
  hotel_name: string;
  role: string;
  role_name: string;
}

export interface User {
  user_id: string;
  email: string;
  full_name: string;
  username?: string;
  tenant_id?: string | null;
  property_id?: string | null;
  group_id?: string | null;
  assigned_properties?: string[];
  active_property_id?: string | null;
  scope_all_properties?: boolean;
  /** Stable role code for the active context: SUPER_ADMIN, HOTEL_OWNER, … */
  role?: string;
  is_superadmin?: boolean;
  is_impersonating?: boolean;
  impersonated_hotel?: string | null;
  impersonation_reason?: string | null;
  roles?: { role_name: string; company_id?: string | null }[];
  permissions?: string[];
  /** Hotels this user may activate from the property switcher. */
  tenants?: HotelMembership[];
  properties?: HotelMembership[];
  partner_id?: string | null;
  partner_name?: string | null;
}

export interface UpdateProfilePayload {
  full_name?: string;
  email?: string;
  current_password?: string;
  new_password?: string;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<string | null>;
  completeAuth: (accessToken: string, user: User) => void;
  logout: () => Promise<void>;
  refreshUser: () => Promise<User | null>;
  updateProfile: (
    payload: UpdateProfilePayload,
  ) => Promise<{ requires_reauth: boolean; message: string }>;
  getAccessToken: () => string | null;
  startImpersonation: (accessToken: string, user: User) => void;
  stopImpersonation: () => Promise<void>;
  switchTenant: (tenantId: string) => Promise<string | null>;
}

const AuthContext = createContext<AuthContextType | null>(null);

// Token stored in module-level variable (memory only, NOT localStorage — XSS-safe)
let accessToken: string | null = null;

// Prevent concurrent refresh attempts
let refreshPromise: Promise<boolean> | null = null;

async function attemptRefresh(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) return false;
    const data = await res.json();
    if (data.status === "ok" && data.access_token) {
      accessToken = data.access_token;
      return true;
    }
    return false;
  } catch {
    return false;
  }
}

export function refreshAuth(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = attemptRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string): void {
  accessToken = token;
}

const IMPERSONATE_KEY = "webclaw_impersonate_tenant";
const IMPERSONATE_REASON_KEY = "webclaw_impersonate_reason";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const fetchMe = useCallback(async () => {
    if (!accessToken) return null;
    const load = async (withTenant: boolean) => {
      const headers: Record<string, string> = {
        Authorization: `Bearer ${accessToken}`,
      };
      if (withTenant) {
        const tenantId = getActiveTenantId();
        if (tenantId) {
          headers["X-Property-ID"] = tenantId;
          if (tenantId.toUpperCase() !== "ALL") {
            headers["X-Tenant-Id"] = tenantId;
          }
        }
      }
      return fetch(`${API_BASE}/auth/me`, { headers });
    };
    try {
      let res = await load(true);
      // A stored property the user can no longer open should not end the session.
      if (res.status === 403 && getActiveTenantId()) {
        setActiveTenantId(null);
        res = await load(false);
      }
      if (!res.ok) return null;
      const data = await res.json();
      if (data.status === "ok") {
        const me = data.user as User;
        bindActiveTenant(me);
        return me;
      }
    } catch {}
    return null;
  }, []);

  const restoreImpersonation = useCallback(async (adminUser: User) => {
    if (typeof window === "undefined") return adminUser;
    const tenantId = sessionStorage.getItem(IMPERSONATE_KEY);
    const reason = sessionStorage.getItem(IMPERSONATE_REASON_KEY) || "";
    if (!tenantId || !adminUser.is_superadmin) return adminUser;
    if (reason.trim().length < 10) {
      sessionStorage.removeItem(IMPERSONATE_KEY);
      sessionStorage.removeItem(IMPERSONATE_REASON_KEY);
      return adminUser;
    }
    try {
      const res = await fetch(`${API_BASE}/admin/tenants/${tenantId}/impersonate`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ reason }),
      });
      if (!res.ok) {
        sessionStorage.removeItem(IMPERSONATE_KEY);
        sessionStorage.removeItem(IMPERSONATE_REASON_KEY);
        return adminUser;
      }
      const data = await res.json();
      if (data.status === "ok" && data.access_token) {
        accessToken = data.access_token;
        return data.user as User;
      }
    } catch {}
    return adminUser;
  }, []);

  // On mount: try to restore session via refresh cookie
  useEffect(() => {
    async function init() {
      const refreshed = await refreshAuth();
      if (refreshed) {
        let me = await fetchMe();
        if (me) me = await restoreImpersonation(me);
        setUser(me);
      }
      setLoading(false);
    }
    init();
  }, [fetchMe, restoreImpersonation]);

  const login = useCallback(
    async (email: string, password: string): Promise<string | null> => {
      try {
        const res = await fetch(`${API_BASE}/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ email, password }),
        });
        const data = await res.json();
        if (data.status !== "ok") return data.message || "Login failed";
        accessToken = data.access_token;
        const next = data.user as User;
        bindActiveTenant(next);
        setUser(next);
        return null; // success
      } catch {
        return "Network error";
      }
    },
    []
  );

  const completeAuth = useCallback((token: string, nextUser: User) => {
    accessToken = token;
    bindActiveTenant(nextUser);
    setUser(nextUser);
  }, []);

  const logout = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: "POST",
        credentials: "include",
      });
    } catch {}
    if (typeof window !== "undefined") {
      sessionStorage.removeItem(IMPERSONATE_KEY);
      sessionStorage.removeItem(IMPERSONATE_REASON_KEY);
    }
    setActiveTenantId(null);
    accessToken = null;
    setUser(null);
    router.push("/login");
  }, [router]);

  const startImpersonation = useCallback((token: string, nextUser: User) => {
    if (typeof window !== "undefined") {
      if (nextUser.tenant_id) {
        sessionStorage.setItem(IMPERSONATE_KEY, nextUser.tenant_id);
      }
      if (nextUser.impersonation_reason) {
        sessionStorage.setItem(IMPERSONATE_REASON_KEY, nextUser.impersonation_reason);
      }
    }
    accessToken = token;
    bindActiveTenant(nextUser);
    setUser(nextUser);
    router.push(postLoginPath(nextUser));
  }, [router]);

  const stopImpersonation = useCallback(async () => {
    try {
      if (accessToken) {
        await fetch(`${API_BASE}/auth/impersonate/end`, {
          method: "POST",
          headers: { Authorization: `Bearer ${accessToken}` },
        });
      }
    } catch {}
    if (typeof window !== "undefined") {
      sessionStorage.removeItem(IMPERSONATE_KEY);
      sessionStorage.removeItem(IMPERSONATE_REASON_KEY);
    }
    setActiveTenantId(null);
    const refreshed = await refreshAuth();
    if (refreshed) {
      const me = await fetchMe();
      setUser(me);
    } else {
      accessToken = null;
      setUser(null);
    }
    router.push("/superadmin/dashboard");
  }, [fetchMe, router]);

  const switchTenant = useCallback(async (tenantId: string): Promise<string | null> => {
    if (!accessToken) return "Not authenticated";
    try {
      const res = await fetch(`${API_BASE}/auth/switch-tenant`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        credentials: "include",
        body: JSON.stringify({
          tenant_id: tenantId,
          property_id: tenantId,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status !== "ok") {
        return (data.message as string) || "Could not switch hotel";
      }
      const next = data.user as User;
      const active =
        next.active_property_id ||
        (next.scope_all_properties ? "ALL" : next.tenant_id) ||
        tenantId;
      setActiveTenantId(active);
      setUser(next);
      return null;
    } catch {
      return "Network error";
    }
  }, []);

  const refreshUser = useCallback(async () => {
    const me = await fetchMe();
    if (me) setUser(me);
    return me;
  }, [fetchMe]);

  const updateProfile = useCallback(
    async (payload: UpdateProfilePayload) => {
      if (!accessToken) throw new Error("Not authenticated");
      const res = await fetch(`${API_BASE}/auth/profile`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        credentials: "include",
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.status === "error") {
        throw new Error(data.message || `Update failed (${res.status})`);
      }
      if (data.requires_reauth) {
        return {
          requires_reauth: true,
          message: (data.message as string) || "Please log in again.",
        };
      }
      // Merge returned fields into current user without dropping roles/permissions
      setUser((prev) =>
        prev
          ? {
              ...prev,
              email: (data.user?.email as string) ?? prev.email,
              full_name: (data.user?.full_name as string) ?? prev.full_name,
              username: (data.user?.username as string) ?? prev.username,
            }
          : prev,
      );
      return {
        requires_reauth: false,
        message: (data.message as string) || "Profile updated.",
      };
    },
    [],
  );

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        login,
        completeAuth,
        logout,
        refreshUser,
        updateProfile,
        getAccessToken: () => accessToken,
        startImpersonation,
        stopImpersonation,
        switchTenant,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function isHotelUser(user: User | null): boolean {
  if (!user || (user.is_superadmin && !user.is_impersonating)) return false;
  if (user.tenant_id) return true;
  return (
    user.roles?.some((r) =>
      [
        "Hotel Owner",
        "Hotel Admin",
        "General Manager",
        "Hotel Manager",
        "Front Desk",
        "Housekeeping",
        "Hotel Accountant",
        "Branch Accountant",
        "Accountant",
        "Hotel Staff",
      ].includes(r.role_name)
    ) ?? false
  );
}

export function isSuperAdmin(user: User | null): boolean {
  if (!user || user.is_impersonating) return false;
  if (user.is_superadmin) return true;
  return (
    user.roles?.some(
      (r) => r.role_name === "System Manager" || r.role_name === "SUPER_ADMIN",
    ) ?? false
  );
}

/** SuperAdmin or least-privilege platform staff (support / billing / security). */
export function isPlatformOperator(user: User | null): boolean {
  if (isSuperAdmin(user)) return true;
  if (!user || user.is_impersonating) return false;
  const perms = user.permissions || [];
  if (perms.some((p) => p === "platform.admin" || p.startsWith("platform."))) {
    return true;
  }
  return (
    user.roles?.some((r) =>
      [
        "Platform Support",
        "Platform Billing",
        "Platform Security",
        "PLATFORM_SUPPORT",
        "PLATFORM_BILLING",
        "PLATFORM_SECURITY",
      ].includes(r.role_name),
    ) ?? false
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
