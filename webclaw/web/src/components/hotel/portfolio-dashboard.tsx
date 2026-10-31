"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Building2,
  CircleDollarSign,
  DoorOpen,
  Loader2,
  Percent,
  Users,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import {
  formatPortfolioMoney,
  loadPortfolioSnapshot,
  type BranchMetrics,
} from "@/lib/owner-properties";

function KpiCard({
  label,
  value,
  subtitle,
  icon,
}: {
  label: string;
  value: string;
  subtitle?: string;
  icon: React.ReactNode;
}) {
  return (
    <Card className="kpi-card">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {label}
        </CardTitle>
        <div className="text-muted-foreground">{icon}</div>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold tracking-tight">{value}</div>
        {subtitle ? (
          <p className="mt-1 text-xs text-muted-foreground">{subtitle}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RevenueByBranchBars({
  branches,
  currency,
}: {
  branches: BranchMetrics[];
  currency: string;
}) {
  const max = Math.max(...branches.map((b) => b.monthly_revenue), 1);
  return (
    <div className="space-y-3">
      {branches.map((b) => {
        const pct = Math.max(2, Math.round((b.monthly_revenue / max) * 100));
        return (
          <div key={b.property_id} className="space-y-1">
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="truncate font-medium">{b.hotel_name}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {formatPortfolioMoney(b.monthly_revenue, currency)}
              </span>
            </div>
            <div className="h-2.5 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary/80 transition-all"
                style={{ width: `${pct}%` }}
                title={`${pct}% of top branch`}
              />
            </div>
          </div>
        );
      })}
      {branches.length === 0 ? (
        <p className="text-sm text-muted-foreground">No branches yet.</p>
      ) : null}
    </div>
  );
}

/**
 * Consolidated Group Owner dashboard when X-Property-ID=ALL.
 */
export function PortfolioDashboard() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["portfolio-snapshot"],
    queryFn: loadPortfolioSnapshot,
    staleTime: 30_000,
  });

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
        Loading portfolio…
      </div>
    );
  }

  if (isError || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Portfolio unavailable</CardTitle>
          <CardDescription>
            {(error as Error)?.message ||
              "Could not load consolidated branch metrics."}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const { branches, totals, currency } = data;
  const sortedByRevenue = [...branches].sort(
    (a, b) => b.monthly_revenue - a.monthly_revenue,
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary" className="gap-1">
          <Building2 className="h-3 w-3" />
          {branches.length} {branches.length === 1 ? "branch" : "branches"}
        </Badge>
        <span className="text-sm text-muted-foreground">
          Aggregated across your hotel group
        </span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Total Combined Revenue"
          value={formatPortfolioMoney(totals.revenue, currency)}
          subtitle="All-time collected payments"
          icon={<CircleDollarSign className="h-4 w-4" />}
        />
        <KpiCard
          label="Portfolio Occupancy"
          value={`${totals.occupancy_pct}%`}
          subtitle="Occupied rooms ÷ total inventory"
          icon={<Percent className="h-4 w-4" />}
        />
        <KpiCard
          label="Total Rooms"
          value={totals.rooms.toLocaleString()}
          subtitle="Across all branches"
          icon={<DoorOpen className="h-4 w-4" />}
        />
        <KpiCard
          label="Active Guests"
          value={totals.guests.toLocaleString()}
          subtitle="On reserved or in-house stays"
          icon={<Users className="h-4 w-4" />}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Revenue by Branch</CardTitle>
            <CardDescription>
              Monthly collected payments (from the 1st of this month)
            </CardDescription>
          </CardHeader>
          <CardContent>
            <RevenueByBranchBars
              branches={sortedByRevenue}
              currency={currency}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Monthly totals</CardTitle>
            <CardDescription>Portfolio roll-up for the current month</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-end justify-between gap-4">
              <div>
                <p className="text-sm text-muted-foreground">Combined monthly revenue</p>
                <p className="text-3xl font-bold tabular-nums">
                  {formatPortfolioMoney(totals.monthly_revenue, currency)}
                </p>
              </div>
              <Badge variant="outline">MTD</Badge>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Branch</TableHead>
                  <TableHead className="text-right">Share</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedByRevenue.map((b) => {
                  const share =
                    totals.monthly_revenue > 0
                      ? Math.round(
                          (b.monthly_revenue / totals.monthly_revenue) * 1000,
                        ) / 10
                      : 0;
                  return (
                    <TableRow key={`share-${b.property_id}`}>
                      <TableCell className="font-medium">{b.hotel_name}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {share}%
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Branch performance</CardTitle>
          <CardDescription>
            Room count, occupancy, monthly revenue, and active staff per location
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Branch Name</TableHead>
                <TableHead className="text-right">Room Count</TableHead>
                <TableHead className="text-right">Occupancy %</TableHead>
                <TableHead className="text-right">Monthly Revenue</TableHead>
                <TableHead className="text-right">Active Staff</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {branches.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground">
                    No properties registered under this group yet.
                  </TableCell>
                </TableRow>
              ) : (
                [...branches]
                  .sort((a, b) => a.hotel_name.localeCompare(b.hotel_name))
                  .map((b) => (
                    <TableRow key={b.property_id}>
                      <TableCell className="font-medium">{b.hotel_name}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {b.rooms.toLocaleString()}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {b.occupancy_pct}%
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatPortfolioMoney(b.monthly_revenue, b.currency)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {b.staff == null ? "—" : b.staff.toLocaleString()}
                      </TableCell>
                    </TableRow>
                  ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
