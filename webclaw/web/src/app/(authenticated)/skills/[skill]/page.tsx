"use client";

import { useEffect, useState, use } from "react";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Loader2, TrendingUp, DollarSign, AlertCircle, Clock, Plus, ArrowRight } from "lucide-react";
import { fetchApi, skillDisplayName, type Skill } from "@/lib/api";
import { useUIConfig } from "@/lib/ui-config";
import { useSkills } from "@/lib/hooks";
import type { DashboardConfig, KpiDef, QuickActionDef } from "@/lib/ui-yaml-types";
import { slugFromListAction, getEntityListUrl, getEntityNewUrl, getActionRunnerUrl } from "@/lib/entity-routing";
import { getListActions } from "@/lib/ui-yaml-to-form";
import { isFinancialCoreSkill, isPmsFinancialRelatedAction, filterFinancialCoreActions } from "@/lib/domain-presets";

// ── Icon resolution ──────────────────────────────────────────────────────────

const ICON_MAP: Record<string, React.ReactNode> = {
  "trending-up": <TrendingUp className="h-4 w-4" />,
  "dollar-sign": <DollarSign className="h-4 w-4" />,
  "alert-circle": <AlertCircle className="h-4 w-4" />,
  clock: <Clock className="h-4 w-4" />,
  plus: <Plus className="h-4 w-4" />,
};

function resolveIcon(name?: string) {
  return name && ICON_MAP[name] ? ICON_MAP[name] : <TrendingUp className="h-4 w-4" />;
}

function kpiId(kpi: KpiDef): string {
  return `${kpi.key}:${kpi.action}`;
}

// ── KPI Card ─────────────────────────────────────────────────────────────────

function severityColor(s?: string) {
  switch (s) {
    case "warning": return "kpi-card-warning";
    case "success": return "kpi-card-success";
    default: return "kpi-card";
  }
}

function KpiCard({
  kpi,
  value,
  subtitle,
  loading,
  onClick,
}: {
  kpi: KpiDef;
  value: string | number | null;
  subtitle?: string;
  loading: boolean;
  onClick?: () => void;
}) {
  return (
    <Card
      className={`${severityColor(kpi.severity)} ${onClick ? "cursor-pointer hover:bg-slate-50 transition-colors" : ""}`}
      onClick={onClick}
    >
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
          {resolveIcon(kpi.icon)}
          {kpi.label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        ) : (
          <>
            <p className="text-2xl font-bold">
              {kpi.type === "currency" && value !== null ? `$${Number(value).toLocaleString()}` : String(value ?? 0)}
            </p>
            {subtitle && <p className="text-xs text-muted-foreground mt-1">{subtitle}</p>}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Main: Skill Dashboard ────────────────────────────────────────────────────

export default function SkillDashboardPage({
  params,
}: {
  params: Promise<{ skill: string }>;
}) {
  const { skill } = use(params);
  const router = useRouter();
  const { config: uiConfig, loading: uiLoading, generating } = useUIConfig(skill);
  const displayName = skillDisplayName(skill);
  const { data: skillsList } = useSkills();
  const skillMeta = skillsList?.find((s) => s.name === skill) ?? null;
  const [kpiValues, setKpiValues] = useState<Record<string, { value: string | number | null; subtitle?: string; loading: boolean }>>({});

  // Load KPI values
  useEffect(() => {
    if (!uiConfig?.dashboard?.kpis) return;

    const kpis = uiConfig.dashboard.kpis;
    // Initialize all as loading
    const init: typeof kpiValues = {};
    for (const kpi of kpis) {
      init[kpiId(kpi)] = { value: null, loading: true };
    }
    setKpiValues(init);

    // Fetch each KPI independently
    for (const kpi of kpis) {
      const id = kpiId(kpi);
      const params = new URLSearchParams();
      params.set("limit", "0");
      if (kpi.filter) {
        for (const [k, v] of Object.entries(kpi.filter)) {
          params.set(k, String(v));
        }
      }
      fetchApi(`/${skill}/${kpi.action}?${params.toString()}`)
        .then((data) => {
          const field = kpi.field || "total_count";
          const raw = data[field] ?? data.total_count ?? null;
          const val = raw !== null ? (typeof raw === "number" ? raw : String(raw)) : null;
          setKpiValues((prev) => ({
            ...prev,
            [id]: { value: val, loading: false },
          }));

          // Fetch total (for subtitle like "of $52,000")
          if (kpi.total_action) {
            fetchApi(`/${skill}/${kpi.total_action}?limit=0`)
              .then((totalData) => {
                const totalField = kpi.total_field || kpi.field || "total_count";
                const totalVal = totalData[totalField] ?? totalData.total_count;
                if (totalVal != null) {
                  const prefix = kpi.type === "currency" ? "of $" : "of ";
                  setKpiValues((prev) => ({
                    ...prev,
                    [id]: {
                      ...prev[id],
                      subtitle: `${prefix}${Number(totalVal).toLocaleString()}`,
                    },
                  }));
                }
              })
              .catch(() => {});
          }
        })
        .catch(() => {
          setKpiValues((prev) => ({
            ...prev,
            [id]: { value: null, loading: false },
          }));
        });
    }
  }, [skill, uiConfig]); // eslint-disable-line react-hooks/exhaustive-deps

  // Get entity list info for KPI drill-down navigation
  const entityListActions = uiConfig
    ? (isFinancialCoreSkill(skill)
        ? getListActions(uiConfig).filter((a) => isPmsFinancialRelatedAction(a.action))
        : getListActions(uiConfig))
    : [];
  // Prefer curated Financial Core labels when present
  const browseActions = isFinancialCoreSkill(skill)
    ? filterFinancialCoreActions(entityListActions)
    : entityListActions;

  function handleKpiClick(kpi: KpiDef) {
    if (kpi.drill_action) {
      const slug = slugFromListAction(kpi.drill_action);
      const params = kpi.drill_filter ? `?${new URLSearchParams(kpi.drill_filter as Record<string, string>).toString()}` : "";
      router.push(getEntityListUrl(skill, slug) + params);
    } else if (kpi.action.startsWith("list-")) {
      const slug = slugFromListAction(kpi.action);
      const filterParams = kpi.filter ? `?${new URLSearchParams(kpi.filter as Record<string, string>).toString()}` : "";
      router.push(getEntityListUrl(skill, slug) + filterParams);
    }
  }

  function handleQuickAction(qa: QuickActionDef) {
    if (qa.action.startsWith("add-") || qa.action.startsWith("create-")) {
      // Navigate to entity new form
      const entityPart = qa.action.replace(/^(add|create)-/, "");
      const slug = entityPart.endsWith("y")
        ? entityPart.slice(0, -1) + "ies"
        : entityPart.endsWith("s") || entityPart.endsWith("x") || entityPart.endsWith("ch") || entityPart.endsWith("sh")
          ? entityPart + "es"
          : entityPart + "s";
      router.push(getEntityNewUrl(skill, slug));
    } else {
      router.push(getActionRunnerUrl(skill, qa.action));
    }
  }

  // HotelPilot dashboard lives at /dashboard — redirect home
  useEffect(() => {
    if (skill === "webclaw") router.replace("/dashboard");
  }, [skill, router]);

  // Loading state — show "generating" message if auto-generating from SKILL.md
  if (uiLoading || skill === "webclaw") {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        {generating && (
          <p className="text-sm text-muted-foreground animate-pulse">
            Building {displayName} interface...
          </p>
        )}
      </div>
    );
  }

  // No UI.yaml and auto-generation failed: fall back to action runner
  if (!uiConfig) {
    return (
      <div className="p-6 space-y-4">
        <div>
          <h2 className="text-xl font-semibold">{displayName}</h2>
          {skillMeta?.description && (
            <p className="text-muted-foreground mt-1">{String(skillMeta.description)}</p>
          )}
        </div>
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground mb-4">
              Could not generate a UI for this skill. Use the action runner to execute commands directly.
            </p>
            <Button onClick={() => router.push(getActionRunnerUrl(skill))}>
              Open Action Runner <ArrowRight className="h-4 w-4 ml-2" />
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const dashboard = uiConfig.dashboard;
  const financialCore = isFinancialCoreSkill(skill);
  const kpis = (dashboard?.kpis || []).filter(
    (kpi) => !financialCore || isPmsFinancialRelatedAction(kpi.action) || isPmsFinancialRelatedAction(kpi.drill_action || "")
  );
  const quickActions = (dashboard?.quick_actions || []).filter(
    (qa) => !financialCore || isPmsFinancialRelatedAction(qa.action)
  );

  return (
    <div className="p-6 space-y-6">
      {/* Skill description */}
      {skillMeta && (
        <div>
          <p className="text-muted-foreground">
            {financialCore
              ? "PMS accounting ledger — chart of accounts, journals, invoices, payments, and tax."
              : String(skillMeta.description || "")}
          </p>
          {!financialCore && (
            <div className="mt-2 flex gap-2">
              <Badge>{["Basic","Standard","Advanced","Professional","Enterprise"][Number(skillMeta.tier)] ?? `Tier ${skillMeta.tier}`}</Badge>
              <Badge variant="secondary">{String(skillMeta.category)}</Badge>
            </div>
          )}
        </div>
      )}

      {/* KPI Cards */}
      {kpis.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {kpis.map((kpi) => {
            const kv = kpiValues[kpiId(kpi)];
            return (
              <KpiCard
                key={kpiId(kpi)}
                kpi={kpi}
                value={kv?.value ?? null}
                subtitle={kv?.subtitle}
                loading={kv?.loading ?? true}
                onClick={() => handleKpiClick(kpi)}
              />
            );
          })}
        </div>
      )}

      {/* Quick Actions */}
      <div className="space-y-4">
        {quickActions.length > 0 && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Quick Actions</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2">
                {quickActions.map((qa) => (
                  <Button
                    key={qa.action}
                    variant="outline"
                    size="sm"
                    className="gap-1.5"
                    onClick={() => handleQuickAction(qa)}
                  >
                    {resolveIcon(qa.icon)}
                    {qa.label}
                  </Button>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {browseActions.length > 0 && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Browse Data</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2">
                {browseActions.map((item) => (
                  <Button
                    key={item.action}
                    variant="outline"
                    size="sm"
                    className="capitalize"
                    onClick={() => {
                      const slug = slugFromListAction(item.action);
                      router.push(getEntityListUrl(skill, slug));
                    }}
                  >
                    {item.label}
                  </Button>
                ))}
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Action Runner link */}
      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={() => router.push(getActionRunnerUrl(skill))}>
          Advanced: Action Runner <ArrowRight className="h-3.5 w-3.5 ml-1" />
        </Button>
      </div>
    </div>
  );
}
