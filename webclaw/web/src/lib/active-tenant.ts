/**
 * Active property for API calls. Persisted in localStorage (with sessionStorage
 * migration) so a refresh keeps the branch the user last chose.
 *
 * Sent as ``X-Property-ID`` (and legacy ``X-Tenant-Id``) on every request.
 * Group owners may set ``ALL`` to aggregate reads across the portfolio.
 */

import { useSyncExternalStore } from "react";

export const PROPERTY_ALL = "ALL";

const ACTIVE_TENANT_KEY = "webclaw_active_tenant";
const ACTIVE_PROPERTY_KEY = "webclaw_active_property_id";

type Listener = () => void;
const listeners = new Set<Listener>();

function readStored(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const fromLocal =
      localStorage.getItem(ACTIVE_PROPERTY_KEY) ||
      localStorage.getItem(ACTIVE_TENANT_KEY);
    if (fromLocal) return fromLocal;
    // Migrate legacy sessionStorage value once.
    const fromSession = sessionStorage.getItem(ACTIVE_TENANT_KEY);
    if (fromSession) {
      localStorage.setItem(ACTIVE_PROPERTY_KEY, fromSession);
      localStorage.setItem(ACTIVE_TENANT_KEY, fromSession);
      sessionStorage.removeItem(ACTIVE_TENANT_KEY);
      return fromSession;
    }
  } catch {
    /* private mode */
  }
  return null;
}

let activeTenantId: string | null = null;
if (typeof window !== "undefined") {
  activeTenantId = readStored();
}

function notify(): void {
  listeners.forEach((l) => l());
}

export function getActiveTenantId(): string | null {
  return activeTenantId;
}

/** Alias used by portfolio / switcher UI. */
export function getActivePropertyId(): string | null {
  return activeTenantId;
}

export function isPortfolioScope(propertyId?: string | null): boolean {
  const id = propertyId ?? activeTenantId;
  return Boolean(id && id.toUpperCase() === PROPERTY_ALL);
}

export function setActiveTenantId(tenantId: string | null): void {
  activeTenantId = tenantId;
  if (typeof window !== "undefined") {
    try {
      if (tenantId) {
        localStorage.setItem(ACTIVE_PROPERTY_KEY, tenantId);
        localStorage.setItem(ACTIVE_TENANT_KEY, tenantId);
        sessionStorage.setItem(ACTIVE_TENANT_KEY, tenantId);
      } else {
        localStorage.removeItem(ACTIVE_PROPERTY_KEY);
        localStorage.removeItem(ACTIVE_TENANT_KEY);
        sessionStorage.removeItem(ACTIVE_TENANT_KEY);
      }
    } catch {
      /* ignore quota / private mode */
    }
  }
  notify();
}

export function setActivePropertyId(propertyId: string | null): void {
  setActiveTenantId(propertyId);
}

export function subscribeActiveProperty(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Reactive active property id for React components. */
export function useActivePropertyId(): string | null {
  return useSyncExternalStore(subscribeActiveProperty, getActivePropertyId, () => null);
}

export function bindActiveTenant(user: {
  tenant_id?: string | null;
  active_property_id?: string | null;
  is_superadmin?: boolean;
  is_impersonating?: boolean;
  tenants?: { tenant_id: string }[];
  assigned_properties?: string[];
  scope_all_properties?: boolean;
}): void {
  if (user.is_superadmin && !user.is_impersonating) {
    setActiveTenantId(null);
    return;
  }
  const allowed = new Set((user.tenants || []).map((t) => t.tenant_id));
  if (user.tenant_id) allowed.add(user.tenant_id);
  for (const p of user.assigned_properties || []) allowed.add(p);
  allowed.add(PROPERTY_ALL);
  const saved = getActiveTenantId();
  if (saved && allowed.has(saved)) return;
  if (user.scope_all_properties) {
    setActiveTenantId(PROPERTY_ALL);
    return;
  }
  const next =
    (user.active_property_id && user.active_property_id !== PROPERTY_ALL
      ? user.active_property_id
      : null) ||
    user.tenant_id ||
    user.tenants?.[0]?.tenant_id ||
    null;
  setActiveTenantId(next);
}
