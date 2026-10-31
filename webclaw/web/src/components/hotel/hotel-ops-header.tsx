"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen,
  CalendarDays,
  CircleDollarSign,
  ClipboardList,
  Receipt,
  Settings2,
  Sparkles,
  BarChart3,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { HotelPermissions } from "@/lib/rbac";

export function HotelOpsHeader({
  title,
  subtitle,
  perms,
  actions,
}: {
  title: string;
  subtitle?: string;
  perms: HotelPermissions;
  actions?: React.ReactNode;
}) {
  const pathname = usePathname();
  const links = [
    perms.canSeeFrontDesk
      ? { href: "/app/hotel/desk", label: "Front desk", icon: CalendarDays, exact: true }
      : null,
    perms.canToggleRoomStatus
      ? { href: "/app/hotel/housekeeping", label: "Housekeeping", icon: Sparkles, exact: false }
      : null,
    perms.canManageRooms
      ? { href: "/app/hotel/inventory", label: "Inventory & rates", icon: Settings2, exact: false }
      : null,
    perms.canSeeFolios
      ? { href: "/app/hotel/billing", label: "Billing", icon: Receipt, exact: false }
      : null,
    perms.canGenerateReports || perms.canSeeFolios
      ? { href: "/app/hotel/reports", label: "Reports", icon: ClipboardList, exact: false }
      : null,
    perms.canSeeFolios
      ? { href: "/app/hotel/revenue", label: "Revenue", icon: CircleDollarSign, exact: false }
      : null,
    perms.canGenerateReports
      ? { href: "/app/hotel/analytics", label: "Analytics", icon: BarChart3, exact: false }
      : null,
    perms.canSeeFinance
      ? { href: "/app/hotel/finance", label: "Finance & Accounting", icon: BookOpen, exact: false }
      : null,
  ].filter(Boolean) as { href: string; label: string; icon: typeof CalendarDays; exact: boolean }[];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
          {subtitle ? <p className="text-sm text-muted-foreground mt-1">{subtitle}</p> : null}
        </div>
        {actions}
      </div>
      <nav className="flex flex-wrap gap-1 border-b">
        {links.map((item) => {
          const Icon = item.icon;
          const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "inline-flex items-center gap-2 px-3 py-2 text-sm border-b-2 -mb-px transition-colors",
                active
                  ? "border-primary text-foreground font-medium bg-accent"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-accent",
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
