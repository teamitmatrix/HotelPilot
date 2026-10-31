"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Building2,
  CalendarDays,
  DollarSign,
  Download,
  Layers,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  downloadPlatformCohortsCsv,
  getPlatformAnalytics,
  getPlatformCohorts,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

function money(n: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(n);
}

export default function SuperAdminPage() {
  const [dimension, setDimension] = useState("plan");
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-analytics"],
    queryFn: getPlatformAnalytics,
  });
  const { data: cohortData, isLoading: cohortsLoading } = useQuery({
    queryKey: ["admin-cohorts", dimension],
    queryFn: () => getPlatformCohorts({ dimension }),
  });

  useEffect(() => {
    document.title = "Platform Analytics — HotelPilot Central Admin";
  }, []);

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Platform Analytics & Reports</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Platform overview</h2>
          <p className="text-muted-foreground">
            Hotels, subscriptions, cohorts, and exportable reports.
          </p>
        </div>

        {error ? (
          <p className="text-sm text-destructive">{(error as Error).message}</p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {(
              [
                {
                  label: "Active hotels",
                  value: data?.active_hotels ?? 0,
                  hint: `${data?.total_hotels ?? 0} total · ${data?.suspended_hotels ?? 0} paused · ${data?.onboarding_hotels ?? 0} onboarding`,
                  icon: Building2,
                },
                {
                  label: "MRR",
                  value: money(data?.subscription_mrr ?? 0),
                  hint: `${data?.active_subscriptions ?? 0} active subscriptions`,
                  icon: DollarSign,
                },
                {
                  label: "Rooms & bookings",
                  value: data?.total_rooms ?? 0,
                  hint: `${data?.active_bookings ?? 0} active · ${data?.booking_count ?? 0} total bookings`,
                  icon: CalendarDays,
                },
                {
                  label: "Hotel admins",
                  value: data?.active_admin_users ?? 0,
                  hint: data?.health?.label ?? "Platform health",
                  icon: Layers,
                },
              ] as const
            ).map((stat) => {
              const Icon = stat.icon;
              return (
                <Card key={stat.label} className="kpi-card">
                  <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                    <CardTitle className="text-sm font-medium text-muted-foreground">
                      {stat.label}
                    </CardTitle>
                    <Icon className="h-4 w-4 text-muted-foreground" />
                  </CardHeader>
                  <CardContent>
                    <div className="text-2xl font-bold">
                      {isLoading ? "—" : stat.value}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{stat.hint}</p>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}

        <Card>
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">Cohorts & churn</CardTitle>
              <p className="text-sm text-muted-foreground">
                Breakdown by plan, country, partner, or signup month.
              </p>
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <div className="space-y-1">
                <Label>Dimension</Label>
                <Select value={dimension} onValueChange={setDimension}>
                  <SelectTrigger className="w-[160px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="plan">Plan</SelectItem>
                    <SelectItem value="country">Country</SelectItem>
                    <SelectItem value="partner">Partner</SelectItem>
                    <SelectItem value="month">Signup month</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Button
                variant="outline"
                onClick={() => downloadPlatformCohortsCsv({ dimension })}
              >
                <Download className="h-4 w-4" />
                Export CSV
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {cohortsLoading ? (
              <p className="text-sm text-muted-foreground">Loading cohorts…</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{dimension}</TableHead>
                    <TableHead>Hotels</TableHead>
                    <TableHead>Active</TableHead>
                    <TableHead>Suspended</TableHead>
                    <TableHead>Cancelled</TableHead>
                    <TableHead>Churn %</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(cohortData?.cohorts || []).length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-muted-foreground">
                        No cohort data
                      </TableCell>
                    </TableRow>
                  ) : (
                    (cohortData?.cohorts || []).map((c) => (
                      <TableRow key={c.key}>
                        <TableCell className="font-medium">{c.key}</TableCell>
                        <TableCell>{c.hotels}</TableCell>
                        <TableCell>{c.active}</TableCell>
                        <TableCell>{c.suspended}</TableCell>
                        <TableCell>{c.cancelled}</TableCell>
                        <TableCell>{c.churn_rate}%</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <Link href="/properties">
              <Building2 className="h-4 w-4" />
              Manage hotels
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/subscriptions">
              <Layers className="h-4 w-4" />
              Subscription plans
            </Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
