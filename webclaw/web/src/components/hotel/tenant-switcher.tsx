"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  Check,
  ChevronsUpDown,
  Layers,
  Plus,
  Settings2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuth, type User } from "@/lib/auth";
import { roleLabel } from "@/lib/auth-routing";
import { useToast } from "@/components/toast-provider";
import { hasPermission } from "@/lib/rbac";
import {
  PROPERTY_ALL,
  isPortfolioScope,
  useActivePropertyId,
} from "@/lib/active-tenant";
import { fetchOwnerProperties } from "@/lib/owner-properties";

function isGroupOwnerRole(user: User): boolean {
  if (user.role === "HOTEL_OWNER" || user.role === "HOTEL_ADMIN") return true;
  if (hasPermission(user, "hotel.manage")) return true;
  return (user.roles || []).some((r) =>
    ["Hotel Owner", "HOTEL_OWNER", "Hotel Admin", "HOTEL_ADMIN"].includes(
      r.role_name,
    ),
  );
}

function propertyLabel(
  name: string | undefined,
  fallback = "Property",
): string {
  return (name || fallback).trim() || fallback;
}

/**
 * Global property selector in the hotel header.
 *
 * Owners always get the dropdown (even with one branch) so they can open
 * consolidated view and jump to "Add branch". Single-property branch staff
 * only see the property name.
 */
export function HotelContextBar() {
  const { user, switchTenant, refreshUser } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<string | null>(null);
  const activeId = useActivePropertyId();

  const isOwner = Boolean(user && isGroupOwnerRole(user));

  const ownerPropsQ = useQuery({
    queryKey: ["owner-properties", user?.user_id],
    queryFn: fetchOwnerProperties,
    enabled: Boolean(user && isOwner),
    staleTime: 30_000,
  });

  const memberships = useMemo(() => {
    if (!user) return [];
    const raw = user.tenants ?? user.properties ?? [];
    const byId = new Map<
      string,
      { tenant_id: string; hotel_name: string; role: string; role_name: string }
    >();
    for (const m of raw) {
      if (m?.tenant_id) {
        byId.set(m.tenant_id, {
          tenant_id: m.tenant_id,
          hotel_name: m.hotel_name || "Hotel",
          role: m.role || "",
          role_name: m.role_name || "",
        });
      }
    }
    // Enrich / extend from owner properties API (covers new branches).
    for (const p of ownerPropsQ.data?.properties || []) {
      if (!p.property_id) continue;
      const existing = byId.get(p.property_id);
      byId.set(p.property_id, {
        tenant_id: p.property_id,
        hotel_name: p.hotel_name || existing?.hotel_name || "Hotel",
        role: existing?.role || user.role || "HOTEL_OWNER",
        role_name: existing?.role_name || "Hotel Owner",
      });
    }
    return Array.from(byId.values());
  }, [user, ownerPropsQ.data]);

  if (!user || user.is_superadmin || user.is_impersonating) return null;
  if (!user.tenant_id && memberships.length === 0) return null;

  const isAll = isPortfolioScope(activeId) || Boolean(user.scope_all_properties);
  const resolvedActive =
    activeId || user.active_property_id || user.tenant_id || null;

  const activeMembership =
    !isAll && resolvedActive
      ? memberships.find((t) => t.tenant_id === resolvedActive)
      : undefined;

  const hotelName = isAll
    ? "All Properties (Consolidated)"
    : propertyLabel(activeMembership?.hotel_name, "Hotel");

  // Owners always see the switcher. Multi-property staff do too.
  // Single-property Front Desk / Housekeeping: name only.
  const showDropdown = isOwner || memberships.length > 1;
  const canUsePortfolio = isOwner;
  const groupReady = Boolean(user.group_id || ownerPropsQ.data?.group_id);

  const role = roleLabel(activeMembership?.role || user.role);

  async function choose(propertyId: string) {
    if (!user || pending) return;
    if (propertyId === PROPERTY_ALL && isAll) return;
    if (propertyId !== PROPERTY_ALL && propertyId === resolvedActive && !isAll) {
      return;
    }
    setPending(propertyId);
    const err = await switchTenant(propertyId);
    setPending(null);
    if (err) {
      // First-time owners may need a refresh after lazy group provisioning.
      if (propertyId === PROPERTY_ALL) {
        await refreshUser();
        await queryClient.invalidateQueries({ queryKey: ["owner-properties"] });
      }
      showToast({ type: "error", message: err });
      return;
    }
    await queryClient.invalidateQueries();
    showToast({
      type: "success",
      message:
        propertyId === PROPERTY_ALL
          ? "Viewing all properties (consolidated)"
          : `Switched to ${
              memberships.find((m) => m.tenant_id === propertyId)?.hotel_name ||
              "property"
            }`,
    });
  }

  return (
    <div className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-background px-4 text-foreground">
      {showDropdown ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="sm"
              className="gap-2"
              disabled={pending !== null}
              aria-label="Switch property"
            >
              {isAll ? (
                <Layers className="h-4 w-4" />
              ) : (
                <Building2 className="h-4 w-4" />
              )}
              <span className="max-w-[260px] truncate">{hotelName}</span>
              <ChevronsUpDown className="h-3.5 w-3.5 text-muted-foreground" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-80">
            <DropdownMenuLabel>Branch / property</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {canUsePortfolio ? (
              <DropdownMenuItem
                onSelect={() => choose(PROPERTY_ALL)}
                className="flex items-start gap-2"
                disabled={!groupReady && memberships.length < 1}
              >
                <Check
                  className={`mt-0.5 h-4 w-4 shrink-0 ${isAll ? "opacity-100" : "opacity-0"}`}
                />
                <span className="min-w-0">
                  <span className="block truncate font-medium">
                    🏢 All Properties (Consolidated)
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    Portfolio KPIs across every branch
                  </span>
                </span>
              </DropdownMenuItem>
            ) : null}
            {canUsePortfolio && memberships.length > 0 ? (
              <DropdownMenuSeparator />
            ) : null}
            {memberships.map((tenant) => {
              const selected = !isAll && tenant.tenant_id === resolvedActive;
              return (
                <DropdownMenuItem
                  key={tenant.tenant_id}
                  onSelect={() => choose(tenant.tenant_id)}
                  className="flex items-start gap-2"
                >
                  <Check
                    className={`mt-0.5 h-4 w-4 shrink-0 ${selected ? "opacity-100" : "opacity-0"}`}
                  />
                  <span className="min-w-0">
                    <span className="block truncate font-medium">
                      {propertyLabel(tenant.hotel_name)}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {roleLabel(tenant.role || tenant.role_name)}
                    </span>
                  </span>
                </DropdownMenuItem>
              );
            })}
            {isOwner ? (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link
                    href="/app/hotel/branches"
                    className="flex cursor-pointer items-center gap-2"
                  >
                    <Plus className="h-4 w-4" />
                    <span>Add / manage branches</span>
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link
                    href="/app/hotel/branches?new=1"
                    className="flex cursor-pointer items-center gap-2"
                  >
                    <Settings2 className="h-4 w-4" />
                    <span>Register new branch</span>
                  </Link>
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
        <div className="flex items-center gap-2 text-sm font-medium">
          <Building2 className="h-4 w-4 text-muted-foreground" />
          <span className="max-w-[260px] truncate">{hotelName}</span>
        </div>
      )}
      <div className="ml-auto min-w-0 text-right text-sm">
        <p className="truncate font-medium">{user.full_name}</p>
        {role ? (
          <p className="truncate text-xs text-muted-foreground">{role}</p>
        ) : null}
      </div>
    </div>
  );
}

/** @deprecated Prefer HotelContextBar — kept for existing imports. */
export { HotelContextBar as PropertySwitcher };
