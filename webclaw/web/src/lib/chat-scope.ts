/**
 * Resolve AI Assistant session scope from auth + active property + route.
 * Used for system-prompt context and the chat header badge.
 */

import type { User } from "./auth";
import { isHotelUser, isSuperAdmin } from "./auth";
import { roleLabel } from "./auth-routing";
import {
  PROPERTY_ALL,
  getActivePropertyId,
  isPortfolioScope,
} from "./active-tenant";

export type ChatViewMode =
  | "platform_superadmin"
  | "hotel_ops"
  | "hotel_impersonation"
  | "generic";

export type SchemaScope = "platform" | "hotel" | "portfolio";

export interface ChatSessionScope {
  viewMode: ChatViewMode;
  is_superadmin: boolean;
  user_role?: string;
  user_role_label?: string;
  hotel_id?: string | null;
  hotel_name?: string | null;
  current_branch?: string | null;
  current_branch_name?: string | null;
  group_id?: string | null;
  scope_all_properties?: boolean;
  schema_scope: SchemaScope;
  /** Subtle badge text shown in the chat header */
  scope_badge: string;
  route?: string;
}

function membershipName(
  user: User,
  propertyId: string | null | undefined,
): string | null {
  if (!propertyId || propertyId.toUpperCase() === PROPERTY_ALL) return null;
  const list = user.properties?.length ? user.properties : user.tenants;
  const hit = list?.find((m) => m.tenant_id === propertyId);
  return hit?.hotel_name || user.impersonated_hotel || null;
}

export function resolveChatSessionScope(
  user: User | null,
  pathname: string,
  activePropertyId?: string | null,
): ChatSessionScope {
  const propertyId = activePropertyId ?? getActivePropertyId();
  const route = pathname || undefined;

  if (isSuperAdmin(user)) {
    return {
      viewMode: "platform_superadmin",
      is_superadmin: true,
      user_role: user?.role || "SUPER_ADMIN",
      user_role_label: roleLabel(user?.role || "SUPER_ADMIN") || "Super Admin",
      hotel_id: null,
      hotel_name: null,
      current_branch: null,
      current_branch_name: null,
      group_id: user?.group_id ?? null,
      scope_all_properties: true,
      schema_scope: "platform",
      scope_badge: "Platform Global Admin",
      route,
    };
  }

  if (user?.is_impersonating) {
    const hotelName =
      user.impersonated_hotel ||
      membershipName(user, propertyId || user.tenant_id) ||
      "Hotel";
    const branchId =
      propertyId && !isPortfolioScope(propertyId)
        ? propertyId
        : user.active_property_id || user.tenant_id || null;
    return {
      viewMode: "hotel_impersonation",
      is_superadmin: false,
      user_role: user.role || "HOTEL_OWNER",
      user_role_label: roleLabel(user.role) || "Hotel Owner",
      hotel_id: user.tenant_id || branchId,
      hotel_name: hotelName,
      current_branch: branchId,
      current_branch_name: membershipName(user, branchId) || hotelName,
      group_id: user.group_id ?? null,
      scope_all_properties: isPortfolioScope(propertyId),
      schema_scope: isPortfolioScope(propertyId) ? "portfolio" : "hotel",
      scope_badge: `Hotel View: ${hotelName}`,
      route,
    };
  }

  if (isHotelUser(user) || pathname.startsWith("/app/hotel")) {
    const portfolio = isPortfolioScope(propertyId);
    const branchId = portfolio
      ? null
      : propertyId || user?.active_property_id || user?.tenant_id || null;
    const hotelName =
      membershipName(user!, branchId) ||
      membershipName(user!, user?.tenant_id) ||
      "Hotel";
    const role = user?.role || "HOTEL_STAFF";
    const badge = portfolio
      ? "Hotel View: All Properties"
      : `Hotel View: ${hotelName}`;

    return {
      viewMode: "hotel_ops",
      is_superadmin: false,
      user_role: role,
      user_role_label: roleLabel(role) || role,
      hotel_id: user?.tenant_id || branchId,
      hotel_name: hotelName,
      current_branch: branchId,
      current_branch_name: portfolio ? "All Properties" : hotelName,
      group_id: user?.group_id ?? null,
      scope_all_properties: portfolio || Boolean(user?.scope_all_properties),
      schema_scope: portfolio ? "portfolio" : "hotel",
      scope_badge: badge,
      route,
    };
  }

  return {
    viewMode: "generic",
    is_superadmin: false,
    user_role: user?.role,
    user_role_label: roleLabel(user?.role),
    hotel_id: user?.tenant_id ?? null,
    hotel_name: null,
    current_branch: propertyId,
    current_branch_name: null,
    group_id: user?.group_id ?? null,
    scope_all_properties: Boolean(user?.scope_all_properties),
    schema_scope: "hotel",
    scope_badge: "App View",
    route,
  };
}
