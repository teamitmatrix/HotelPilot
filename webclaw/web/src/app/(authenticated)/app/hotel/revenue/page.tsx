"use client";

import { useMemo } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { useEntityList } from "@/lib/hooks";
import {
  SKILL,
  type RevenueBooking,
  formatMoney,
  guestName,
  hotelCurrency,
} from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { CircleDollarSign, Loader2 } from "lucide-react";

function statusLabel(status: string | null | undefined): string {
  return (status || "—").replace(/_/g, " ");
}

export default function HotelRevenuePage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);

  const revenueQ = useEntityList(
    SKILL,
    "list-revenue",
    { limit: "200" },
    { enabled: perms.canSeeFolios, refetchInterval: false },
  );

  const currency = hotelCurrency(revenueQ.data?.currency);
  const bookings = useMemo(
    () => (Array.isArray(revenueQ.data?.bookings) ? (revenueQ.data.bookings as RevenueBooking[]) : []),
    [revenueQ.data],
  );
  const revenueTotal = revenueQ.data?.revenue_total;
  const chargesTotal = revenueQ.data?.charges_total;
  const outstandingTotal = revenueQ.data?.outstanding_total;
  const hasTotals = revenueTotal != null;
  const ready = perms.canSeeFolios && !revenueQ.isLoading && !revenueQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Revenue",
      subtitle: "Collected payments by booking",
      kpis: [
        {
          label: "Collected revenue",
          value: hasTotals ? formatMoney(revenueTotal, currency) : "",
        },
        {
          label: "Charges posted",
          value: hasTotals ? formatMoney(chargesTotal, currency) : "",
        },
        {
          label: "Outstanding",
          value: hasTotals ? formatMoney(outstandingTotal, currency) : "",
        },
      ],
      sheets: [
        {
          name: "Bookings",
          headers: [
            "Guest",
            "Email",
            "Room",
            "Check-in",
            "Check-out",
            "Reservation status",
            "Folio status",
            "Charges",
            "Paid",
            "Balance",
          ],
          rows: bookings.map((b) => [
            guestName(b),
            b.guest_email || "",
            b.room_number || b.room_type_name || "",
            b.check_in,
            b.check_out,
            statusLabel(b.reservation_status),
            b.folio_status || "",
            formatMoney(b.charges_total, currency),
            formatMoney(b.payments_total, currency),
            formatMoney(b.folio_balance, currency),
          ]),
        },
        {
          name: "Payments",
          headers: [
            "Guest",
            "Reservation",
            "Amount",
            "Method",
            "Reference",
            "Posted at",
          ],
          rows: bookings.flatMap((b) =>
            b.payments.map((p) => [
              guestName(b),
              b.reservation_id,
              formatMoney(p.amount, currency),
              p.payment_method,
              p.reference || "",
              p.posted_at ? String(p.posted_at).slice(0, 19).replace("T", " ") : "",
            ]),
          ),
        },
      ],
    };
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Revenue</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <ReportsBackLink />
        <HotelOpsHeader
          title="Revenue"
          subtitle="All-time collected payments by booking for this property"
          perms={perms}
          actions={
            <ReportExportButtons
              disabled={!ready}
              filename="revenue"
              buildDoc={buildExportDoc}
            />
          }
        />

        {!perms.canSeeFolios ? (
          <p className="text-sm text-muted-foreground">
            Revenue is hidden for your role. Ask a manager or owner for billing access.
          </p>
        ) : revenueQ.isLoading ? (
          <div className="flex h-48 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : revenueQ.isError ? (
          <Card>
            <CardContent className="p-6 text-sm text-destructive">
              Could not load revenue
              {revenueQ.error instanceof Error ? `: ${revenueQ.error.message}` : "."}
              {" "}Start the API and refresh this page.
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-6">
            <div className="grid gap-4 sm:grid-cols-3">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Collected revenue
                  </CardTitle>
                  <CircleDollarSign className="h-4 w-4 text-muted-foreground" />
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">
                    {hasTotals ? formatMoney(revenueTotal, currency) : "—"}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">All-time posted guest payments</p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Charges posted
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">
                    {hasTotals ? formatMoney(chargesTotal, currency) : "—"}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">Room, tax, and other charges</p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Outstanding
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">
                    {hasTotals ? formatMoney(outstandingTotal, currency) : "—"}
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">Open folio balances</p>
                </CardContent>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Bookings & payments</CardTitle>
              </CardHeader>
              <CardContent className="px-0 pb-0">
                {bookings.length === 0 ? (
                  <p className="px-6 pb-6 text-sm text-muted-foreground">
                    No bookings for this property yet. Settled stays and payments show here for all dates.
                  </p>
                ) : (
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Guest</TableHead>
                          <TableHead>Stay</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead className="text-right">Charges</TableHead>
                          <TableHead className="text-right">Paid</TableHead>
                          <TableHead className="text-right">Balance</TableHead>
                          <TableHead>Payments</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {bookings.map((b) => (
                          <TableRow key={b.reservation_id}>
                            <TableCell>
                              <div className="font-medium">{guestName(b)}</div>
                              <div className="text-xs text-muted-foreground">
                                {b.room_number
                                  ? `Room ${b.room_number}`
                                  : b.room_type_name || "Unassigned"}
                                {b.guest_email ? ` · ${b.guest_email}` : ""}
                              </div>
                            </TableCell>
                            <TableCell className="whitespace-nowrap text-sm">
                              {b.check_in} → {b.check_out}
                            </TableCell>
                            <TableCell>
                              <Badge variant="secondary" className="capitalize">
                                {statusLabel(b.reservation_status)}
                              </Badge>
                              {b.folio_status ? (
                                <div className="mt-1 text-[11px] capitalize text-muted-foreground">
                                  Folio {b.folio_status}
                                </div>
                              ) : null}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatMoney(b.charges_total, currency)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums font-medium">
                              {formatMoney(b.payments_total, currency)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatMoney(b.folio_balance, currency)}
                            </TableCell>
                            <TableCell>
                              {b.payments.length === 0 ? (
                                <span className="text-xs text-muted-foreground">No payments</span>
                              ) : (
                                <ul className="space-y-1.5">
                                  {b.payments.map((p) => (
                                    <li key={p.id} className="text-xs">
                                      <span className="font-medium tabular-nums">
                                        {formatMoney(p.amount, currency)}
                                      </span>
                                      <span className="text-muted-foreground">
                                        {" "}
                                        · {p.payment_method}
                                        {p.reference ? ` · ref ${p.reference}` : ""}
                                      </span>
                                      {p.posted_at ? (
                                        <div className="text-muted-foreground">
                                          {String(p.posted_at).slice(0, 16).replace("T", " ")}
                                        </div>
                                      ) : null}
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
