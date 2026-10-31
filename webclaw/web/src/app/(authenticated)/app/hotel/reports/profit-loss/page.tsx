"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
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
  ReportDateRange,
  ReportExportButtons,
  ReportPageShell,
  monthStartIso,
  todayIso,
} from "@/components/hotel/report-page-shell";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { fetchApi } from "@/lib/api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

type PlLine = { account?: string; account_id?: string; amount?: string };

export default function ProfitLossReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const [fromDate, setFromDate] = useState(monthStartIso);
  const [toDate, setToDate] = useState(todayIso);

  const plQ = useQuery({
    queryKey: ["report-profit-loss", fromDate, toDate],
    queryFn: () =>
      fetchApi(`/erpclaw/profit-and-loss?from_date=${fromDate}&to_date=${toDate}`),
    enabled: perms.canGenerateReports,
  });

  const currency = hotelCurrency(plQ.data?.currency);
  const income = useMemo(
    () => (Array.isArray(plQ.data?.income) ? (plQ.data.income as PlLine[]) : []),
    [plQ.data],
  );
  const expenses = useMemo(
    () => (Array.isArray(plQ.data?.expenses) ? (plQ.data.expenses as PlLine[]) : []),
    [plQ.data],
  );

  const ready = perms.canGenerateReports && !plQ.isLoading && !plQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Profit & Loss",
      subtitle: `${fromDate} to ${toDate}`,
      kpis: [
        { label: "Income", value: formatMoney(plQ.data?.income_total, currency) },
        { label: "Expenses", value: formatMoney(plQ.data?.expense_total, currency) },
        { label: "Net income", value: formatMoney(plQ.data?.net_income, currency) },
      ],
      sheets: [
        {
          name: "Income",
          headers: ["Account", "Amount"],
          rows: income.map((row) => [row.account || "", formatMoney(row.amount, currency)]),
        },
        {
          name: "Expenses",
          headers: ["Account", "Amount"],
          rows: expenses.map((row) => [row.account || "", formatMoney(row.amount, currency)]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Profit & Loss"
      subtitle="Income and expense statement for the selected period (read-only)"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <ReportDateRange
            fromDate={fromDate}
            toDate={toDate}
            onFromChange={setFromDate}
            onToChange={setToDate}
          />
          <ReportExportButtons
            disabled={!ready}
            filename={`profit-loss-${fromDate}-${toDate}`}
            buildDoc={buildExportDoc}
          />
        </div>
      }
    >
      {!perms.canGenerateReports ? (
        <p className="text-sm text-muted-foreground">
          This report requires Hotel Admin, General Manager, or Manager access.
        </p>
      ) : plQ.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : plQ.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            Could not load P&amp;L
            {plQ.error instanceof Error ? `: ${plQ.error.message}` : "."}
            {" "}Ensure the property company chart of accounts is set up.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <Kpi
              label="Income"
              value={formatMoney(plQ.data?.income_total, currency)}
            />
            <Kpi
              label="Expenses"
              value={formatMoney(plQ.data?.expense_total, currency)}
            />
            <Kpi
              label="Net income"
              value={formatMoney(plQ.data?.net_income, currency)}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <AccountTable title="Income" rows={income} currency={currency} />
            <AccountTable title="Expenses" rows={expenses} currency={currency} />
          </div>

          {plQ.data?.period ? (
            <p className="text-xs text-muted-foreground">Period: {String(plQ.data.period)}</p>
          ) : null}
        </div>
      )}
    </ReportPageShell>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
      </CardContent>
    </Card>
  );
}

function AccountTable({
  title,
  rows,
  currency,
}: {
  title: string;
  rows: PlLine[];
  currency: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Account</TableHead>
              <TableHead className="text-right">Amount</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={2} className="py-8 text-center text-muted-foreground">
                  No {title.toLowerCase()} lines in this period.
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row, idx) => (
                <TableRow key={row.account_id || `${row.account}-${idx}`}>
                  <TableCell>{row.account || "—"}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoney(row.amount, currency)}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
