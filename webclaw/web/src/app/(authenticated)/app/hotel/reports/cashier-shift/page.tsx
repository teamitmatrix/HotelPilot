"use client";

import { useMemo, useState } from "react";
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
import {
  ReportExportButtons,
  ReportPageShell,
  todayIso,
} from "@/components/hotel/report-page-shell";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useEntityList } from "@/lib/hooks";
import { SKILL, formatMoney, hotelCurrency, itemsFrom } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

type TxRow = {
  id: string;
  amount?: string | number;
  payment_method?: string;
  status?: string;
  posted_at?: string | null;
  created_at?: string | null;
  reference?: string | null;
  folio_id?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  guest_email?: string | null;
  room_number?: string | null;
};

function guestLabel(tx: TxRow) {
  const name = [tx.first_name, tx.last_name].filter(Boolean).join(" ").trim();
  return name || tx.guest_email || "—";
}

export default function CashierShiftReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canGenerateReports || perms.canSeeFolios;
  const [shiftDate, setShiftDate] = useState(todayIso);
  const [fromTime, setFromTime] = useState("00:00");
  const [toTime, setToTime] = useState("23:59");

  const fromStamp = `${shiftDate}T${fromTime}:00`;
  const toStamp = `${shiftDate}T${toTime}:59`;

  const txQ = useEntityList(
    SKILL,
    "list-transactions",
    {
      limit: "500",
      from_date: fromStamp,
      to_date: toStamp,
      status: "posted",
    },
    { enabled: canView, refetchInterval: false },
  );

  const currency = hotelCurrency(txQ.data?.currency);
  const transactions = useMemo(() => {
    const rows = itemsFrom<TxRow>(txQ.data, "transactions");
    return [...rows].sort((a, b) =>
      String(b.posted_at || b.created_at || "").localeCompare(
        String(a.posted_at || a.created_at || ""),
      ),
    );
  }, [txQ.data]);

  const totals = useMemo(() => {
    const byMethod: Record<string, number> = {};
    let total = 0;
    for (const tx of transactions) {
      const amt = parseFloat(String(tx.amount || "0")) || 0;
      total += amt;
      const method = (tx.payment_method || "other").toLowerCase();
      byMethod[method] = (byMethod[method] || 0) + amt;
    }
    return { total, byMethod, count: transactions.length };
  }, [transactions]);

  const ready = canView && !txQ.isLoading && !txQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Cashier / shift report",
      subtitle: `${fromStamp} → ${toStamp}`,
      kpis: [
        { label: "Payments", value: totals.count },
        { label: "Collected", value: formatMoney(totals.total, currency) },
        ...Object.entries(totals.byMethod).map(([method, amt]) => ({
          label: method,
          value: formatMoney(amt, currency),
        })),
      ],
      sheets: [
        {
          name: "Payments",
          headers: ["Posted", "Guest", "Room", "Method", "Amount", "Reference", "Folio"],
          rows: transactions.map((tx) => [
            String(tx.posted_at || tx.created_at || "").slice(0, 19).replace("T", " "),
            guestLabel(tx),
            tx.room_number || "",
            tx.payment_method || "",
            formatMoney(tx.amount, currency),
            tx.reference || "",
            tx.folio_id || "",
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Cashier / shift report"
      subtitle="Posted payments for a shift window — totals by payment method"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <Label className="text-xs text-muted-foreground">Shift date</Label>
            <Input
              type="date"
              className="w-[150px]"
              value={shiftDate}
              onChange={(e) => setShiftDate(e.target.value)}
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">From</Label>
            <Input
              type="time"
              className="w-[120px]"
              value={fromTime}
              onChange={(e) => setFromTime(e.target.value)}
            />
          </div>
          <div>
            <Label className="text-xs text-muted-foreground">To</Label>
            <Input
              type="time"
              className="w-[120px]"
              value={toTime}
              onChange={(e) => setToTime(e.target.value)}
            />
          </div>
          <ReportExportButtons
            disabled={!ready}
            filename={`cashier-shift-${shiftDate}`}
            buildDoc={buildExportDoc}
          />
        </div>
      }
    >
      {!canView ? (
        <p className="text-sm text-muted-foreground">
          This report requires Manager or billing access.
        </p>
      ) : txQ.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : txQ.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            Could not load payments
            {txQ.error instanceof Error ? `: ${txQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi label="Payments" value={String(totals.count)} />
            <Kpi label="Collected" value={formatMoney(totals.total, currency)} />
            {Object.entries(totals.byMethod)
              .slice(0, 2)
              .map(([method, amt]) => (
                <Kpi key={method} label={method} value={formatMoney(amt, currency)} />
              ))}
          </div>

          {Object.keys(totals.byMethod).length > 2 ? (
            <div className="flex flex-wrap gap-2">
              {Object.entries(totals.byMethod).map(([method, amt]) => (
                <Badge key={method} variant="secondary" className="capitalize">
                  {method}: {formatMoney(amt, currency)}
                </Badge>
              ))}
            </div>
          ) : null}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Shift payments</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Posted</TableHead>
                    <TableHead>Guest</TableHead>
                    <TableHead>Room</TableHead>
                    <TableHead>Method</TableHead>
                    <TableHead className="text-right">Amount</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {transactions.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                        No posted payments in this shift window.
                      </TableCell>
                    </TableRow>
                  ) : (
                    transactions.map((tx) => (
                      <TableRow key={tx.id}>
                        <TableCell className="whitespace-nowrap text-muted-foreground">
                          {String(tx.posted_at || tx.created_at || "")
                            .slice(0, 16)
                            .replace("T", " ")}
                        </TableCell>
                        <TableCell>
                          <div className="font-medium">{guestLabel(tx)}</div>
                          <div className="text-xs text-muted-foreground">
                            {tx.reference || tx.folio_id?.slice(0, 8) || "—"}
                          </div>
                        </TableCell>
                        <TableCell>{tx.room_number || "—"}</TableCell>
                        <TableCell className="capitalize">{tx.payment_method || "—"}</TableCell>
                        <TableCell className="text-right tabular-nums font-medium">
                          {formatMoney(tx.amount, currency)}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}
    </ReportPageShell>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground capitalize">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
      </CardContent>
    </Card>
  );
}
