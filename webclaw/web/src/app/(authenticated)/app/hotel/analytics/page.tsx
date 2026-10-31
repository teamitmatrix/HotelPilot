"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { HotelOpsHeader } from "@/components/hotel/hotel-ops-header";
import { ReportExportButtons, ReportsBackLink } from "@/components/hotel/report-page-shell";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { getPerformanceAnalytics } from "@/lib/analytics-api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import {
  Loader2,
  Percent,
  BedDouble,
  CircleDollarSign,
  TrendingUp,
  CalendarRange,
  Ban,
} from "lucide-react";

const SOURCE_COLORS = ["#0f766e", "#0369a1", "#b45309", "#7c3aed", "#be123c"];

function monthStart(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

function tomorrowIso(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toISOString().slice(0, 10);
}

function KpiCard({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
        <div className="text-muted-foreground">{icon}</div>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold tracking-tight">{value}</div>
        {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}

export default function HotelAnalyticsPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const [fromDate, setFromDate] = useState(monthStart);
  const [toDate, setToDate] = useState(tomorrowIso);

  const analyticsQ = useQuery({
    queryKey: ["hotel-analytics", fromDate, toDate],
    queryFn: () => getPerformanceAnalytics(fromDate, toDate),
    enabled: perms.canSeeFolios,
  });

  const data = analyticsQ.data;
  const currency = hotelCurrency(data?.hotel?.currency);
  const trendData = useMemo(() => data?.revenue_trend?.aligned || [], [data]);
  const sourceData = useMemo(
    () =>
      (data?.booking_sources?.sources || []).map((s) => ({
        name: s.label,
        value: s.bookings,
        revenue: Number(s.revenue || 0),
        share: Number(s.share_pct || 0),
      })),
    [data],
  );
  const branchBars = useMemo(
    () =>
      (data?.branches || []).map((b) => ({
        name: b.hotel_name,
        revenue: Number(b.room_revenue || 0),
        occupancy: Number(b.occupancy_pct || 0),
        revpar: Number(b.revpar || 0),
      })),
    [data],
  );

  const ready = perms.canSeeFolios && !!data && !analyticsQ.isLoading && !analyticsQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!data) return null;
    const kpis = data.kpis;
    return {
      title: "Performance analytics",
      subtitle: data.period?.label || `${fromDate} to ${toDate}`,
      kpis: [
        { label: "Room revenue", value: formatMoney(kpis.room_revenue, currency) },
        { label: "RevPAR", value: formatMoney(kpis.revpar, currency) },
        { label: "ADR", value: formatMoney(kpis.adr, currency) },
        { label: "Occupancy %", value: kpis.occupancy_pct },
        { label: "ALOS", value: kpis.alos },
        { label: "Cancellation rate %", value: kpis.cancellation_rate_pct },
        { label: "Rooms sold", value: kpis.rooms_sold },
        { label: "Available room-nights", value: kpis.available_room_nights },
      ],
      sheets: [
        {
          name: "Occupancy",
          headers: ["Date", "Room revenue", "Rooms sold", "Rooms available", "Occupancy %"],
          rows: (data.occupancy_table || []).map((row) => [
            row.date,
            row.room_revenue,
            row.rooms_sold,
            row.rooms_available,
            row.occupancy_pct,
          ]),
        },
        {
          name: "Booking sources",
          headers: ["Source", "Bookings", "Room nights", "Revenue", "Share %"],
          rows: (data.booking_sources?.sources || []).map((src) => [
            src.label,
            src.bookings,
            src.room_nights,
            src.revenue,
            src.share_pct,
          ]),
        },
        {
          name: "Branches",
          headers: ["Branch", "Room revenue", "Occupancy %", "ADR", "RevPAR", "ALOS", "Cancel %"],
          rows: (data.branches || []).map((b) => [
            b.hotel_name,
            b.room_revenue,
            b.occupancy_pct,
            b.adr,
            b.revpar,
            b.alos,
            b.cancellation_rate_pct,
          ]),
        },
      ],
    };
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Analytics</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <ReportsBackLink />
        <HotelOpsHeader
          title="Business intelligence"
          subtitle="RevPAR, ADR, occupancy, booking mix, and branch comparison"
          perms={perms}
          actions={
            perms.canSeeFolios ? (
              <ReportExportButtons
                disabled={!ready}
                filename={`hotel-analytics-${fromDate}-${toDate}`}
                buildDoc={buildExportDoc}
              />
            ) : null
          }
        />

        {!perms.canSeeFolios ? (
          <p className="text-sm text-muted-foreground">
            Analytics is hidden for your role. Ask a manager or owner for billing access.
          </p>
        ) : (
          <>
            <Card>
              <CardContent className="flex flex-wrap items-end gap-4 pt-6">
                <div>
                  <Label>From</Label>
                  <Input
                    type="date"
                    value={fromDate}
                    onChange={(e) => setFromDate(e.target.value)}
                  />
                </div>
                <div>
                  <Label>To (exclusive)</Label>
                  <Input
                    type="date"
                    value={toDate}
                    onChange={(e) => setToDate(e.target.value)}
                  />
                </div>
                <Badge variant="outline" className="mb-1">
                  {data?.period?.label || "Current period"}
                </Badge>
              </CardContent>
            </Card>

            {analyticsQ.isLoading ? (
              <div className="flex h-48 items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : analyticsQ.isError ? (
              <Card>
                <CardContent className="p-6 text-sm text-destructive">
                  Could not load analytics
                  {analyticsQ.error instanceof Error ? `: ${analyticsQ.error.message}` : "."}
                </CardContent>
              </Card>
            ) : data ? (
              <div className="space-y-6">
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  <KpiCard
                    label="RevPAR"
                    value={formatMoney(data.kpis.revpar, currency)}
                    hint="Room revenue ÷ available room-nights"
                    icon={<TrendingUp className="h-4 w-4" />}
                  />
                  <KpiCard
                    label="ADR"
                    value={formatMoney(data.kpis.adr, currency)}
                    hint="Room revenue ÷ rooms sold"
                    icon={<CircleDollarSign className="h-4 w-4" />}
                  />
                  <KpiCard
                    label="Occupancy"
                    value={`${data.kpis.occupancy_pct}%`}
                    hint={`${data.kpis.rooms_sold} sold / ${data.kpis.available_room_nights} available`}
                    icon={<Percent className="h-4 w-4" />}
                  />
                  <KpiCard
                    label="Room revenue"
                    value={formatMoney(data.kpis.room_revenue, currency)}
                    hint={`${data.kpis.rooms_available} sellable rooms`}
                    icon={<BedDouble className="h-4 w-4" />}
                  />
                  <KpiCard
                    label="ALOS"
                    value={`${data.kpis.alos} nights`}
                    hint="Average length of stay"
                    icon={<CalendarRange className="h-4 w-4" />}
                  />
                  <KpiCard
                    label="Cancellation rate"
                    value={`${data.kpis.cancellation_rate_pct}%`}
                    hint={`${data.kpis.cancellations} of ${data.kpis.reservations_created} created in period`}
                    icon={<Ban className="h-4 w-4" />}
                  />
                </div>

                <div className="grid gap-6 xl:grid-cols-5">
                  <Card className="xl:col-span-3">
                    <CardHeader>
                      <CardTitle className="text-base">Revenue trend</CardTitle>
                      <CardDescription>
                        Daily room revenue —{" "}
                        {data.revenue_trend.current_month.label} vs{" "}
                        {data.revenue_trend.previous_month.label} vs{" "}
                        {data.revenue_trend.last_year.label}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="h-80">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={trendData}>
                          <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                          <XAxis dataKey="day" tickLine={false} axisLine={false} />
                          <YAxis tickLine={false} axisLine={false} width={56} />
                          <Tooltip
                            formatter={(value) =>
                              formatMoney(String(value ?? 0), currency)
                            }
                            labelFormatter={(day) => `Day ${day}`}
                          />
                          <Legend />
                          <Line
                            type="monotone"
                            dataKey="current"
                            name={data.revenue_trend.current_month.label}
                            stroke="#0f766e"
                            strokeWidth={2}
                            dot={false}
                          />
                          <Line
                            type="monotone"
                            dataKey="previous_month"
                            name={data.revenue_trend.previous_month.label}
                            stroke="#0369a1"
                            strokeWidth={2}
                            dot={false}
                          />
                          <Line
                            type="monotone"
                            dataKey="last_year"
                            name={data.revenue_trend.last_year.label}
                            stroke="#b45309"
                            strokeWidth={2}
                            strokeDasharray="4 4"
                            dot={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </CardContent>
                  </Card>

                  <Card className="xl:col-span-2">
                    <CardHeader>
                      <CardTitle className="text-base">Booking sources</CardTitle>
                      <CardDescription>
                        Direct walk-ins, website, and OTAs for stays in this period
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="h-80">
                      {sourceData.every((s) => s.value === 0) ? (
                        <p className="text-sm text-muted-foreground">No bookings in this period.</p>
                      ) : (
                        <ResponsiveContainer width="100%" height="100%">
                          <PieChart>
                            <Pie
                              data={sourceData}
                              dataKey="value"
                              nameKey="name"
                              innerRadius={55}
                              outerRadius={90}
                              paddingAngle={2}
                            >
                              {sourceData.map((_, idx) => (
                                <Cell key={idx} fill={SOURCE_COLORS[idx % SOURCE_COLORS.length]} />
                              ))}
                            </Pie>
                            <Tooltip
                              formatter={(value, name, item) => [
                                `${value} bookings (${item?.payload?.share ?? 0}%)`,
                                String(name),
                              ]}
                            />
                            <Legend />
                          </PieChart>
                        </ResponsiveContainer>
                      )}
                    </CardContent>
                  </Card>
                </div>

                {(data.branches?.length || 0) > 1 ? (
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">Branch performance</CardTitle>
                      <CardDescription>
                        Room revenue and occupancy across properties in your group
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                      <div className="h-72">
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={branchBars}>
                            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                            <XAxis dataKey="name" tickLine={false} axisLine={false} />
                            <YAxis tickLine={false} axisLine={false} width={56} />
                            <Tooltip
                              formatter={(value, name) =>
                                name === "occupancy"
                                  ? [`${value}%`, "Occupancy"]
                                  : [formatMoney(String(value ?? 0), currency), String(name)]
                              }
                            />
                            <Legend />
                            <Bar dataKey="revenue" name="Room revenue" fill="#0f766e" radius={4} />
                            <Bar dataKey="revpar" name="RevPAR" fill="#0369a1" radius={4} />
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Branch</TableHead>
                            <TableHead>Revenue</TableHead>
                            <TableHead>Occupancy</TableHead>
                            <TableHead>ADR</TableHead>
                            <TableHead>RevPAR</TableHead>
                            <TableHead>ALOS</TableHead>
                            <TableHead>Cancel %</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {data.branches.map((b) => (
                            <TableRow key={b.property_id}>
                              <TableCell className="font-medium">{b.hotel_name}</TableCell>
                              <TableCell>{formatMoney(b.room_revenue, currency)}</TableCell>
                              <TableCell>{b.occupancy_pct}%</TableCell>
                              <TableCell>{formatMoney(b.adr, currency)}</TableCell>
                              <TableCell>{formatMoney(b.revpar, currency)}</TableCell>
                              <TableCell>{b.alos}</TableCell>
                              <TableCell>{b.cancellation_rate_pct}%</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </CardContent>
                  </Card>
                ) : null}

                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Occupancy table</CardTitle>
                    <CardDescription>Nightly sold rooms, availability, and room revenue</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Date</TableHead>
                          <TableHead>Sold</TableHead>
                          <TableHead>Available</TableHead>
                          <TableHead>Occupancy</TableHead>
                          <TableHead>Room revenue</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(data.occupancy_table || []).map((row) => (
                          <TableRow key={row.date}>
                            <TableCell>{row.date}</TableCell>
                            <TableCell>{row.rooms_sold}</TableCell>
                            <TableCell>{row.rooms_available}</TableCell>
                            <TableCell>{row.occupancy_pct}%</TableCell>
                            <TableCell>{formatMoney(row.room_revenue, currency)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
