"use client";

import Link from "next/link";
import { AlertTriangle, AlertCircle, Info, ArrowRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useAlerts, type Alert } from "@/lib/alerts";

const SEVERITY_CONFIG: Record<Alert["severity"], {
  icon: typeof AlertTriangle;
  badgeClass: string;
  borderClass: string;
}> = {
  critical: {
    icon: AlertCircle,
    badgeClass: "border-transparent bg-red-600 text-white",
    borderClass: "kpi-card-warning",
  },
  warning: {
    icon: AlertTriangle,
    badgeClass: "border border-red-200 bg-red-50 text-red-700",
    borderClass: "kpi-card-warning",
  },
  info: {
    icon: Info,
    badgeClass: "border-transparent bg-navy-900 text-white",
    borderClass: "kpi-card",
  },
};

export function AlertCards() {
  const { data: alerts = [] } = useAlerts();

  if (alerts.length === 0) return null;

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-amber-500" />
        Alerts
      </h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {alerts.map((alert, i) => {
          const config = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.info;
          const Icon = config.icon;
          return (
            <Link key={i} href={alert.link}>
              <Card className={`${config.borderClass} transition-colors hover:bg-slate-50 cursor-pointer`}>
                <CardContent className="pt-4 pb-3">
                  <div className="flex items-center gap-3">
                    <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium">{alert.message}</p>
                    </div>
                    <Badge className={`shrink-0 ${config.badgeClass}`}>
                      {alert.count}
                    </Badge>
                    <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                  </div>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
