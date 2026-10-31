"use client";

import { useEffect, useState, useMemo } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarFooter,
  useSidebar,
} from "@/components/ui/sidebar";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Building2,
  CalendarDays,
  DoorOpen,
  BedDouble,
  BookOpen,
  Receipt,
  BarChart3,
  Package,
  ShoppingCart,
  Truck,
  Factory,
  Users,
  FolderKanban,
  HeartPulse,
  Headset,
  LayoutDashboard,
  LogOut,
  ChevronUp,
  ChevronRight,
  Shield,
  AlertTriangle,
  Search,
  Lock,
  Scale,
  Puzzle,
  Settings,
  Layers,
  Clock,
  ArrowLeft,
  Circle,
  Plug,
  Ticket,
  ScrollText,
  CreditCard,
  Megaphone,
  UserRound,
  Sparkles,
  Banknote,
  Flag,
  Gauge,
  Paintbrush,
  KeyRound,
  Handshake,
  ListChecks,
  Webhook,
  ClipboardList,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { EditProfileDialog } from "@/components/edit-profile-dialog";
import {
  type Skill,
  type SuiteGroup,
  getSkills,
  skillDisplayName,
  CATEGORY_CONFIG,
  categoryLabel,
  detectSuites,
} from "@/lib/api";
import { useAuth, isHotelUser, isPlatformOperator, isSuperAdmin } from "@/lib/auth";
import { hasPermission } from "@/lib/rbac";
import { useCurrentProfile } from "@/lib/adaptive";
import { useUIConfig } from "@/lib/ui-config";
import { getListActions } from "@/lib/ui-yaml-to-form";
import { slugFromListAction, getEntityListUrl } from "@/lib/entity-routing";
import {
  isFinancialCoreSkill,
  filterFinancialCoreActions,
} from "@/lib/domain-presets";

// Category-based icons (extensible)
const CATEGORY_ICONS: Record<string, React.ElementType> = {
  setup: Building2,
  accounting: BookOpen,
  inventory: Package,
  selling: ShoppingCart,
  buying: Truck,
  manufacturing: Factory,
  hr: Users,
  projects: FolderKanban,
  crm: HeartPulse,
  billing: Receipt,
  support: Headset,
  analytics: BarChart3,
  compliance: Shield,
  risk: AlertTriangle,
  audit: Search,
  security: Lock,
  governance: Scale,
  "property-management": Building2,
  hospitality: BedDouble,
};

const PLATFORM_TITLE =
  process.env.NEXT_PUBLIC_OCUI_TITLE || "HotelPilot Central Admin";
const HOTEL_TITLE =
  process.env.NEXT_PUBLIC_HOTEL_TITLE || "HotelPilot";

const HOTEL_NAV = [
  { href: "/app/hotel/desk", label: "Front desk", icon: CalendarDays, permission: "bookings.read" as const, exact: true },
  { href: "/app/hotel/housekeeping", label: "Housekeeping", icon: Sparkles, permission: "rooms.update_status" as const, exact: false },
  { href: "/app/hotel/inventory", label: "Inventory & rates", icon: Settings, permission: "rooms.write" as const, exact: false },
  { href: "/app/hotel/billing", label: "Billing", icon: Receipt, permission: "folios.read" as const, exact: false },
  {
    href: "/app/hotel/reports",
    label: "Reports",
    icon: ClipboardList,
    permission: "reports.generate" as const,
    altPermissions: ["folios.read"] as const,
    exact: false,
  },
  {
    href: "/app/hotel/revenue",
    label: "Revenue",
    icon: Banknote,
    permission: "reports.generate" as const,
    altPermissions: ["folios.read"] as const,
    exact: false,
  },
  {
    href: "/app/hotel/analytics",
    label: "Analytics",
    icon: BarChart3,
    permission: "reports.generate" as const,
    exact: false,
  },
  {
    href: "/app/hotel/finance",
    label: "Finance & Accounting",
    icon: BookOpen,
    permission: "ledger.write" as const,
    altPermissions: ["finance.night_audit", "finance.tax_reports", "finance.refunds"] as const,
    exact: false,
  },
];

type SuperAdminNavItem = {
  href: string;
  label: string;
  icon: React.ElementType;
  /** Match pathname exactly (ignore query). */
  exact?: boolean;
  /** Match when pathname starts with this prefix (defaults to href without query). */
  matchPrefix?: string;
};

const PARTNER_NAV: SuperAdminNavItem[] = [
  { href: "/partner", label: "Partner console", icon: Handshake, exact: true },
];

const SUPERADMIN_NAV: SuperAdminNavItem[] = [
  { href: "/superadmin/dashboard", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { href: "/properties", label: "Properties / Hotels", icon: Building2 },
  { href: "/onboarding", label: "Onboarding Pipeline", icon: ListChecks },
  { href: "/subscriptions", label: "Subscriptions & Plans", icon: CreditCard },
  { href: "/settings?tab=users", label: "Hotel Accounts / Users", icon: Users },
  { href: "/skills/erpclaw", label: "Financial Core", icon: BookOpen, matchPrefix: "/skills/erpclaw" },
  { href: "/settings?tab=integrations", label: "System Integrations", icon: Plug },
  { href: "/superadmin/support", label: "Support & Tickets", icon: Ticket },
  { href: "/superadmin", label: "Platform Analytics & Reports", icon: BarChart3, exact: true },
  { href: "/settings?tab=roles", label: "Roles & Permissions", icon: Shield },
  { href: "/audit-logs", label: "Audit Logs", icon: ScrollText },
  { href: "/announcements", label: "Announcements", icon: Megaphone },
  { href: "/security", label: "Security & Backups", icon: Lock },
  { href: "/feature-flags", label: "Feature Flags", icon: Flag },
  { href: "/usage", label: "Usage & Add-ons", icon: Gauge },
  { href: "/white-label", label: "White-label", icon: Paintbrush },
  { href: "/identity", label: "SSO / SCIM", icon: KeyRound },
  { href: "/compliance", label: "Compliance / DSAR", icon: Scale },
  { href: "/partners", label: "Partners / Resellers", icon: Handshake },
  { href: "/api-platform", label: "API & Webhooks", icon: Webhook },
  { href: "/settings?tab=platform", label: "Platform Settings", icon: Settings },
];

function isNavActive(
  pathname: string,
  searchParams: URLSearchParams | null,
  item: SuperAdminNavItem,
): boolean {
  const pathOnly = item.href.split("?")[0];
  const tab = new URLSearchParams(item.href.split("?")[1] || "").get("tab");

  if (pathOnly === "/settings") {
    if (!pathname.startsWith("/settings")) return false;
    // SuperAdmin default tab is platform when query is omitted.
    const currentTab = searchParams?.get("tab") || "platform";
    return tab ? currentTab === tab : false;
  }

  if (item.exact) {
    return pathname === pathOnly;
  }

  const prefix = item.matchPrefix || pathOnly;
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

function PartnerNav({ pathname }: { pathname: string }) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Reseller</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {PARTNER_NAV.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <SidebarMenuItem key={item.href}>
                <SidebarMenuButton asChild isActive={active}>
                  <Link href={item.href}>
                    <Icon className="h-4 w-4" />
                    <span>{item.label}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

function SuperAdminNav({
  pathname,
  searchParams,
}: {
  pathname: string;
  searchParams: URLSearchParams | null;
}) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Platform Management</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {SUPERADMIN_NAV.map((item) => {
            const Icon = item.icon;
            const active = isNavActive(pathname, searchParams, item);
            return (
              <SidebarMenuItem key={`${item.label}-${item.href}`}>
                <SidebarMenuButton asChild isActive={active}>
                  <Link href={item.href}>
                    <Icon className="h-4 w-4" />
                    <span>{item.label}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

function HotelOpsNav({ pathname, user }: { pathname: string; user: ReturnType<typeof useAuth>["user"] }) {
  const { isMobile, setOpenMobile } = useSidebar();
  const items = HOTEL_NAV.filter((item) => {
    if (hasPermission(user, item.permission)) return true;
    const alts = "altPermissions" in item ? item.altPermissions : undefined;
    return Boolean(alts?.some((p) => hasPermission(user, p)));
  });
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Hotel ops</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => {
            const Icon = item.icon;
            const active = item.exact
              ? pathname === item.href
              : pathname.startsWith(item.href);
            return (
              <SidebarMenuItem key={item.href}>
                <SidebarMenuButton
                  asChild
                  isActive={active}
                  size="lg"
                  className="touch-manipulation"
                >
                  <Link
                    href={item.href}
                    onClick={() => {
                      if (isMobile) setOpenMobile(false);
                    }}
                  >
                    <Icon className="h-4 w-4" />
                    <span>{item.label}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

// ── Recent Skills (localStorage) ────────────────────────────────────────────

const RECENT_SKILLS_KEY = "erpclaw_recent_skills";
const MAX_RECENT = 5;

function getRecentSkills(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(RECENT_SKILLS_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.slice(0, MAX_RECENT) : [];
  } catch {
    return [];
  }
}

function pushRecentSkill(skillName: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const current = getRecentSkills();
    const deduped = [skillName, ...current.filter((s) => s !== skillName)].slice(0, MAX_RECENT);
    localStorage.setItem(RECENT_SKILLS_KEY, JSON.stringify(deduped));
    return deduped;
  } catch {
    return [];
  }
}

// ── Skill list rendering (shared between single-suite and multi-suite) ──────

function SkillList({
  skills,
  allSkills,
  pathname,
}: {
  skills: Skill[];
  allSkills: Skill[];
  pathname: string;
}) {
  return (
    <SidebarMenu>
      {skills.map((skill) => {
        const Icon = CATEGORY_ICONS[skill.category || ""] || Puzzle;
        const href = `/skills/${skill.name}`;
        return (
          <SidebarMenuItem key={skill.name}>
            <SidebarMenuButton asChild isActive={pathname.startsWith(href)}>
              <Link href={href}>
                <Icon className="h-4 w-4" />
                <span>{skillDisplayName(skill.name, allSkills)}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}

// ── Category groups (flat view, used in single-suite mode) ──────────────────

function CategoryGroups({
  grouped,
  allSkills,
  pathname,
}: {
  grouped: Record<string, Skill[]>;
  allSkills: Skill[];
  pathname: string;
}) {
  const sortedCategories = Object.keys(grouped).sort(
    (a, b) =>
      (CATEGORY_CONFIG[a]?.order ?? 99) - (CATEGORY_CONFIG[b]?.order ?? 99)
  );

  return (
    <>
      {sortedCategories.map((cat) => (
        <SidebarGroup key={cat}>
          <SidebarGroupLabel>{categoryLabel(cat)}</SidebarGroupLabel>
          <SidebarGroupContent>
            <SkillList
              skills={grouped[cat]}
              allSkills={allSkills}
              pathname={pathname}
            />
          </SidebarGroupContent>
        </SidebarGroup>
      ))}
    </>
  );
}

// ── Suite section (collapsible, used in multi-suite mode) ───────────────────

function SuiteSection({
  suite,
  allSkills,
  pathname,
}: {
  suite: SuiteGroup;
  allSkills: Skill[];
  pathname: string;
}) {
  // Check if any skill in this suite is currently active
  const isActive = suite.skills.some((s) =>
    pathname.startsWith(`/skills/${s.name}`)
  );

  const sortedCategories = Object.keys(suite.categories).sort(
    (a, b) =>
      (CATEGORY_CONFIG[a]?.order ?? 99) - (CATEGORY_CONFIG[b]?.order ?? 99)
  );

  return (
    <Collapsible defaultOpen={isActive || suite.prefix === "erpclaw"}>
      <SidebarGroup>
        <CollapsibleTrigger className="flex w-full items-center gap-2 px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors">
          <Layers className="h-3.5 w-3.5" />
          <span className="flex-1 text-left">{suite.label}</span>
          <span className="text-[10px] font-normal text-muted-foreground/60">
            {suite.skills.length}
          </span>
          <ChevronRight className="h-3.5 w-3.5 transition-transform group-data-[state=open]:rotate-90" />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <SidebarGroupContent>
            {sortedCategories.length > 1 ? (
              // Multiple categories within suite: show sub-groups
              sortedCategories.map((cat) => (
                <div key={cat} className="mb-1">
                  <p className="px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
                    {categoryLabel(cat)}
                  </p>
                  <SkillList
                    skills={suite.categories[cat]}
                    allSkills={suite.skills}
                    pathname={pathname}
                  />
                </div>
              ))
            ) : (
              // Single category: flat list
              <SkillList
                skills={suite.skills}
                allSkills={suite.skills}
                pathname={pathname}
              />
            )}
          </SidebarGroupContent>
        </CollapsibleContent>
      </SidebarGroup>
    </Collapsible>
  );
}

// ── Skill Entity Tree (context-aware: shown when inside a skill) ────────────

function SkillEntityTree({
  skillName,
}: {
  skillName: string;
  skillDisplayLabel: string;
}) {
  const { config: uiConfig, loading } = useUIConfig(skillName);
  const pathname = usePathname();
  const financialCore = isFinancialCoreSkill(skillName);

  // Derive active entity slug from pathname: /skills/propertyclaw/properties → "properties"
  const activeSlug = (() => {
    const match = pathname.match(/^\/skills\/[^/]+\/([^/]+)/);
    return match ? match[1] : "";
  })();

  const entityListActions = useMemo(() => {
    if (!uiConfig) return [];
    const all = getListActions(uiConfig);
    if (financialCore) return filterFinancialCoreActions(all);
    return all;
  }, [uiConfig, financialCore]);

  const filteredDomains = useMemo(() => {
    if (financialCore) return []; // flat curated list below
    return uiConfig?.domains ?? [];
  }, [uiConfig?.domains, financialCore]);

  if (loading) {
    return (
      <div className="px-4 py-3 text-xs text-muted-foreground">
        Loading entities...
      </div>
    );
  }

  if (entityListActions.length === 0) {
    return null;
  }

  const hasDomains = filteredDomains.length > 0;
  const isDashboard = !activeSlug;

  // Financial Core: flat PMS accounting nav (no retail/manufacturing domains)
  if (financialCore) {
    return (
      <>
        <SidebarGroup className="py-1">
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={isDashboard}>
                  <Link href={`/skills/${skillName}`}>
                    <LayoutDashboard className="h-4 w-4" />
                    <span>Dashboard</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarGroup className="py-1">
          <SidebarGroupLabel className="text-[10px] font-semibold tracking-wide">
            Accounting
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {entityListActions.map((item) => (
                <SidebarMenuItem key={item.action}>
                  <SidebarMenuButton
                    asChild
                    isActive={activeSlug === slugFromListAction(item.action)}
                    className="pl-4"
                  >
                    <Link href={getEntityListUrl(skillName, slugFromListAction(item.action))}>
                      <Circle className="h-2 w-2" />
                      <span>{item.label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </>
    );
  }

  if (hasDomains) {
    return (
      <>
        <SidebarGroup className="py-1">
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={isDashboard}>
                  <Link href={`/skills/${skillName}`}>
                    <LayoutDashboard className="h-4 w-4" />
                    <span>Dashboard</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        {filteredDomains.map((domain) => {
          const domainActions = entityListActions.filter(
            (e) => e.domain === domain.key
          );
          if (domainActions.length === 0) return null;
          return (
            <SidebarGroup key={domain.key} className="py-1">
              <SidebarGroupLabel className="text-[10px] font-semibold tracking-wide">
                {domain.label}
              </SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {domainActions.map((item) => (
                    <SidebarMenuItem key={item.action}>
                      <SidebarMenuButton
                        asChild
                        isActive={activeSlug === slugFromListAction(item.action)}
                        className="pl-4"
                      >
                        <Link href={getEntityListUrl(skillName, slugFromListAction(item.action))}>
                          <Circle className="h-2 w-2" />
                          <span className="capitalize">{item.label}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          );
        })}
        {/* Ungrouped entities — skip for Financial Core (curated list is complete) */}
        {!financialCore && (() => {
          const allDomains = uiConfig?.domains ?? [];
          const domainEntities = new Set(
            allDomains.flatMap((d) => d.entities)
          );
          const ungrouped = entityListActions.filter(
            (e) => !domainEntities.has(e.entity)
          );
          if (ungrouped.length === 0) return null;
          return (
            <SidebarGroup className="py-1">
              <SidebarGroupLabel className="text-[10px] font-semibold tracking-wide">
                Other
              </SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {ungrouped.map((item) => (
                    <SidebarMenuItem key={item.action}>
                      <SidebarMenuButton
                        asChild
                        isActive={activeSlug === slugFromListAction(item.action)}
                        className="pl-4"
                      >
                        <Link href={getEntityListUrl(skillName, slugFromListAction(item.action))}>
                          <Circle className="h-2 w-2" />
                          <span className="capitalize">{item.label}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          );
        })()}
        {/* Action Runner — hidden for Financial Core (exposes retail/manufacturing actions) */}
        {!financialCore && (
          <SidebarGroup className="py-1">
            <SidebarGroupContent>
              <SidebarMenu>
                <SidebarMenuItem>
                  <SidebarMenuButton asChild isActive={activeSlug === "actions"} className="text-muted-foreground">
                    <Link href={`/skills/${skillName}/actions`}>
                      <Puzzle className="h-4 w-4" />
                      <span>Action Runner</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        )}
      </>
    );
  }

  // No domains: flat entity list
  return (
    <>
    <SidebarGroup className="py-1">
      <SidebarGroupContent>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild isActive={isDashboard}>
              <Link href={`/skills/${skillName}`}>
                <LayoutDashboard className="h-4 w-4" />
                <span>Dashboard</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
    <SidebarGroup className="py-1">
      <SidebarGroupLabel className="text-[10px] font-semibold tracking-wide">
        Entities
      </SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {entityListActions.map((item) => (
            <SidebarMenuItem key={item.action}>
              <SidebarMenuButton
                asChild
                isActive={activeSlug === slugFromListAction(item.action)}
                className="pl-4"
              >
                <Link href={getEntityListUrl(skillName, slugFromListAction(item.action))}>
                  <Circle className="h-2 w-2" />
                  <span className="capitalize">{item.label}</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
    {!financialCore && (
      <SidebarGroup className="py-1">
        <SidebarGroupContent>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild isActive={activeSlug === "actions"} className="text-muted-foreground">
                <Link href={`/skills/${skillName}/actions`}>
                  <Puzzle className="h-4 w-4" />
                  <span>Action Runner</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    )}
    </>
  );
}

// ── Main sidebar ────────────────────────────────────────────────────────────

export function AppSidebar() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [recentSkills, setRecentSkills] = useState<string[]>([]);
  const [profileOpen, setProfileOpen] = useState(false);
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { user, logout } = useAuth();
  const { isMobile, setOpenMobile } = useSidebar();
  const hotelOps = isHotelUser(user);
  const superAdmin = isPlatformOperator(user) || isSuperAdmin(user);
  const partnerConsole = Boolean(
    user?.partner_id ||
      (user?.permissions || []).includes("partner.console.view") ||
      user?.roles?.some((r) =>
        ["Partner Admin", "PARTNER_ADMIN"].includes(r.role_name),
      ),
  );
  const { data: profile } = useCurrentProfile();

  function closeMobileNav() {
    if (isMobile) setOpenMobile(false);
  }

  useEffect(() => {
    getSkills().then(setSkills).catch(() => {});
    setRecentSkills(getRecentSkills());
  }, []);

  // Filter skills: UI-capable only; SuperAdmin never sees hospitality (tenant ops).
  // Hotel ops also hide hospitalityclaw — they use /app/hotel/* exclusively.
  // Then apply adaptive profile active_skills when present.
  const visibleSkills = useMemo(() => {
    let filtered = skills.filter((s) => s.has_ui);
    if (superAdmin || hotelOps) {
      filtered = filtered.filter(
        (s) => s.name !== "hospitalityclaw" && !s.name.startsWith("hospitalityclaw-")
      );
    }
    if (profile?.active_skills && profile.active_skills.length > 0) {
      const activeSet = new Set(profile.active_skills);
      filtered = filtered.filter((s) => activeSet.has(s.name));
    }
    return filtered;
  }, [skills, profile, superAdmin, hotelOps]);

  // Track recent skill visits
  useEffect(() => {
    const match = pathname.match(/^\/skills\/([^/?]+)/);
    if (match && match[1]) {
      const updated = pushRecentSkill(match[1]);
      setRecentSkills(updated);
    }
  }, [pathname]);

  // Detect if we're inside a skill page
  const currentSkillName = useMemo(() => {
    const match = pathname.match(/^\/skills\/([^/?]+)/);
    return match ? match[1] : null;
  }, [pathname]);

  const isSkillMode = !!currentSkillName;
  const isFinancialCoreMode =
    !!currentSkillName && isFinancialCoreSkill(currentSkillName);

  // Detect multi-suite vs single-suite (from visible skills only)
  const suites = detectSuites(visibleSkills);
  const isMultiSuite = suites.length > 0;

  // Resolve display label for current skill
  const currentSkillLabel = useMemo(() => {
    if (!currentSkillName) return "";
    return skillDisplayName(currentSkillName, skills);
  }, [currentSkillName, skills]);

  // Get skill's category icon
  const CurrentSkillIcon = useMemo(() => {
    if (!currentSkillName) return Puzzle;
    if (isFinancialCoreSkill(currentSkillName)) return BookOpen;
    const skill = skills.find((s) => s.name === currentSkillName);
    return CATEGORY_ICONS[skill?.category || ""] || Puzzle;
  }, [currentSkillName, skills]);

  // Filter visible skills by search query
  const filteredSkills = useMemo(() => {
    if (!searchQuery.trim()) return visibleSkills;
    const q = searchQuery.toLowerCase();
    return visibleSkills.filter((s) => {
      const display = skillDisplayName(s.name, skills).toLowerCase();
      const name = s.name.toLowerCase();
      return display.includes(q) || name.includes(q);
    });
  }, [visibleSkills, skills, searchQuery]);

  // Single-suite mode: group by category (current behaviour)
  const grouped: Record<string, Skill[]> = {};
  const skillsForGrouping = searchQuery.trim() ? filteredSkills : visibleSkills;
  if (!isMultiSuite) {
    for (const s of skillsForGrouping) {
      const cat = s.category || "other";
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(s);
    }
  }

  // Multi-suite mode: filter suites when searching
  const filteredSuites = useMemo(() => {
    if (!searchQuery.trim()) return suites;
    const filteredNames = new Set(filteredSkills.map((s) => s.name));
    return suites
      .map((suite) => ({
        ...suite,
        skills: suite.skills.filter((s) => filteredNames.has(s.name)),
        categories: Object.fromEntries(
          Object.entries(suite.categories)
            .map(([cat, catSkills]) => [cat, catSkills.filter((s) => filteredNames.has(s.name))])
            .filter(([, catSkills]) => (catSkills as Skill[]).length > 0)
        ),
      }))
      .filter((suite) => suite.skills.length > 0);
  }, [suites, filteredSkills, searchQuery]);

  // Recent skills resolved from skill list
  const recentItems = useMemo(() => {
    if (searchQuery.trim()) return []; // Hide recents while searching
    return recentSkills
      .map((name) => visibleSkills.find((s) => s.name === name))
      .filter((s): s is Skill => s !== undefined);
  }, [recentSkills, visibleSkills, searchQuery]);

  const initials = user?.full_name
    ? user.full_name
        .split(" ")
        .map((w) => w[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : "U";

  const title = hotelOps ? HOTEL_TITLE : PLATFORM_TITLE;
  const homeHref = superAdmin
    ? "/superadmin/dashboard"
    : partnerConsole && !hotelOps
      ? "/partner"
      : hotelOps
        ? "/app/hotel/dashboard"
        : "/dashboard";

  // SuperAdmin inside Financial Core: entity tree + back to platform
  const showFinancialCoreTree = superAdmin && isFinancialCoreMode;
  // Legacy skill browser for non-SuperAdmin (and non-hotel) operators
  const showSkillBrowser = !hotelOps && !superAdmin && !partnerConsole;

  return (
    <Sidebar>
      {/* ── Header: context-aware ── */}
      {isSkillMode ? (
        <SidebarHeader className="border-b border-slate-200 bg-navy-900 px-4 py-3 text-white">
          <Link
            href={homeHref}
            className="flex items-center gap-2 text-xs text-white/70 hover:text-white transition-colors mb-1"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span>
              {hotelOps
                ? "Dashboard"
                : superAdmin
                ? PLATFORM_TITLE
                : "Platform"}
            </span>
          </Link>
          <div className="flex items-center gap-2">
            <CurrentSkillIcon className="h-5 w-5 text-white" />
            <span className="text-base font-semibold tracking-tight">
              {currentSkillLabel}
            </span>
          </div>
        </SidebarHeader>
      ) : (
        <SidebarHeader className="border-b border-slate-200 bg-navy-900 px-4 py-3 text-white">
          <Link href={homeHref} className="flex items-center gap-2">
            <Building2 className="h-6 w-6 text-white" />
            <span className="text-lg font-bold tracking-tight leading-tight">
              {title}
            </span>
          </Link>
          <p className="text-xs text-white/70">
            {hotelOps
              ? "Hotel operations"
              : superAdmin
              ? "Platform management"
              : isMultiSuite
              ? `${suites.length} suites`
              : "Property management"}
          </p>
        </SidebarHeader>
      )}

      <SidebarContent>
        {showFinancialCoreTree && currentSkillName ? (
          /* ── Financial Core: PMS accounting entity tree ── */
          <SkillEntityTree
            skillName={currentSkillName}
            skillDisplayLabel={currentSkillLabel}
          />
        ) : isSkillMode && currentSkillName && !superAdmin && !hotelOps ? (
          /* ── Non-admin skill mode (never for hotel ops — they use /app/hotel/*) ── */
          <SkillEntityTree
            skillName={currentSkillName}
            skillDisplayLabel={currentSkillLabel}
          />
        ) : superAdmin ? (
          /* ── SuperAdmin SaaS platform nav (home or non-financial routes) ── */
          <SuperAdminNav pathname={pathname} searchParams={searchParams} />
        ) : partnerConsole ? (
          <PartnerNav pathname={pathname} />
        ) : (
          /* ── Hotel ops or legacy module browser ── */
          <>
            {showSkillBrowser && (
            <div className="px-3 pt-2 pb-1">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
                <Input
                  placeholder="Filter modules..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-8 h-8 text-xs"
                />
              </div>
            </div>
            )}

            {/* Dashboard link */}
            {!searchQuery.trim() && (
              <SidebarGroup>
                <SidebarGroupContent>
                  <SidebarMenu>
                    <SidebarMenuItem>
                      <SidebarMenuButton
                        asChild
                        isActive={pathname === homeHref}
                        size="lg"
                        className="touch-manipulation"
                      >
                        <Link href={homeHref} onClick={closeMobileNav}>
                          <LayoutDashboard className="h-4 w-4" />
                          <span>Dashboard</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  </SidebarMenu>
                </SidebarGroupContent>
              </SidebarGroup>
            )}

            {hotelOps && !searchQuery.trim() && <HotelOpsNav pathname={pathname} user={user} />}

            {/* Recent modules */}
            {showSkillBrowser && recentItems.length > 0 && (
              <SidebarGroup>
                <SidebarGroupLabel className="gap-1.5">
                  <Clock className="h-3 w-3" />
                  Recent
                </SidebarGroupLabel>
                <SidebarGroupContent>
                  <SkillList
                    skills={recentItems}
                    allSkills={skills}
                    pathname={pathname}
                  />
                </SidebarGroupContent>
              </SidebarGroup>
            )}

            {showSkillBrowser && isMultiSuite && filteredSuites.map((suite) => (
              <SuiteSection
                key={suite.prefix}
                suite={suite}
                allSkills={visibleSkills}
                pathname={pathname}
              />
            ))}

            {showSkillBrowser && !isMultiSuite && skillsForGrouping.length > 0 && (
              <CategoryGroups
                grouped={grouped}
                allSkills={skills}
                pathname={pathname}
              />
            )}

            {searchQuery.trim() && filteredSkills.length === 0 && (
              <div className="px-4 py-6 text-center text-xs text-muted-foreground">
                No modules match &ldquo;{searchQuery}&rdquo;
              </div>
            )}
          </>
        )}
      </SidebarContent>
      <SidebarFooter className="border-t p-2">
        {hotelOps &&
          (hasPermission(user, "hotel.manage") ||
            hasPermission(user, "hotel.staff.manage")) && (
          <SidebarMenu>
            {hasPermission(user, "hotel.manage") && (
              <>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    asChild
                    isActive={pathname.startsWith("/app/hotel/branches")}
                  >
                    <Link href="/app/hotel/branches">
                      <Building2 className="h-4 w-4" />
                      <span>Branches</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
                <SidebarMenuItem>
                  <SidebarMenuButton
                    asChild
                    isActive={pathname.startsWith("/app/hotel/settings/channels")}
                  >
                    <Link href="/app/hotel/settings/channels">
                      <Plug className="h-4 w-4" />
                      <span>Channels</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              </>
            )}
            <SidebarMenuItem>
              <SidebarMenuButton
                asChild
                isActive={
                  pathname.startsWith("/app/hotel/settings") &&
                  !pathname.startsWith("/app/hotel/settings/channels")
                }
              >
                <Link
                  href={
                    hasPermission(user, "hotel.manage")
                      ? "/app/hotel/settings"
                      : "/app/hotel/settings?tab=staff"
                  }
                >
                  <Settings className="h-4 w-4" />
                  <span>
                    {hasPermission(user, "hotel.manage")
                      ? "Hotel settings"
                      : "Staff"}
                  </span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        )}
        {!hotelOps && !superAdmin && (
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild isActive={pathname === "/settings"}>
                <Link href="/settings">
                  <Settings className="h-4 w-4" />
                  <span>Settings</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-left text-sm hover:bg-accent">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                {initials}
              </div>
              <div className="flex-1 truncate">
                <p className="truncate text-sm font-medium">
                  {user?.full_name || "User"}
                </p>
                <p className="truncate text-xs text-muted-foreground">
                  {user?.email}
                </p>
              </div>
              <ChevronUp className="h-4 w-4 text-muted-foreground" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56">
            <DropdownMenuItem onClick={() => setProfileOpen(true)}>
              <UserRound className="mr-2 h-4 w-4" />
              Edit profile
            </DropdownMenuItem>
            <DropdownMenuItem onClick={logout}>
              <LogOut className="mr-2 h-4 w-4" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <EditProfileDialog open={profileOpen} onOpenChange={setProfileOpen} />
      </SidebarFooter>
    </Sidebar>
  );
}
