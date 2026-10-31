"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ReportExportButtons, ReportPageShell } from "@/components/hotel/report-page-shell";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useToast } from "@/components/toast-provider";
import {
  downloadNightAuditPdf,
  listNightAuditRuns,
  type NightAuditRun,
} from "@/lib/night-audit-api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Download, Loader2 } from "lucide-react";

function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function NightAuditHistoryPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const runsQ = useQuery({
    queryKey: ["night-audit-history"],
    queryFn: () => listNightAuditRuns(50),
    enabled: perms.canRunNightAudit,
  });

  const currency = hotelCurrency(undefined);

  async function handleDownload(run: NightAuditRun) {
    setDownloadingId(run.id);
    try {
      await downloadNightAuditPdf(run.id, run.business_date);
      showToast({ type: "success", message: "Night audit PDF downloaded" });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Could not download PDF",
      });
    } finally {
      setDownloadingId(null);
    }
  }

  const runs = runsQ.data || [];
  const ready = perms.canRunNightAudit && !runsQ.isLoading && !runsQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Night audit history",
      subtitle: "Completed and failed closeouts",
      kpis: [
        { label: "Runs", value: runs.length },
        {
          label: "Completed",
          value: runs.filter(
            (r) => r.status === "completed" || r.status === "completed_with_exceptions",
          ).length,
        },
        {
          label: "Failed",
          value: runs.filter((r) => r.status === "failed").length,
        },
        {
          label: "Exceptions (latest)",
          value: runs[0]?.exception_count ?? 0,
        },
      ],
      sheets: [
        {
          name: "Runs",
          headers: [
            "Business date",
            "Status",
            "Triggered by",
            "Completed",
            "Room revenue",
            "Occupancy %",
            "Exceptions",
          ],
          rows: runs.map((run) => [
            run.business_date,
            run.status,
            run.triggered_by || "",
            formatWhen(run.completed_at || run.started_at),
            run.kpis?.room_revenue != null
              ? formatMoney(run.kpis.room_revenue, currency)
              : "",
            run.kpis?.occupancy_rate_pct != null ? run.kpis.occupancy_rate_pct : "",
            run.exception_count ?? 0,
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Night audit history"
      subtitle="Completed and failed closeouts for this property — Admin and Accountant only"
      actions={
        <div className="flex flex-wrap items-center gap-2">
          {perms.canRunNightAudit ? (
            <Button asChild variant="outline" size="sm">
              <Link href="/app/hotel/finance">Run night audit</Link>
            </Button>
          ) : null}
          <ReportExportButtons
            disabled={!ready}
            filename="night-audit-history"
            buildDoc={buildExportDoc}
          />
        </div>
      }
    >
      {!perms.canRunNightAudit ? (
        <p className="text-sm text-muted-foreground">
          Night audit history is limited to Hotel Admin and Accountant roles.
        </p>
      ) : runsQ.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : runsQ.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            Could not load night audit history
            {runsQ.error instanceof Error ? `: ${runsQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Recent runs</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Business date</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Triggered by</TableHead>
                  <TableHead>Completed</TableHead>
                  <TableHead>Room revenue</TableHead>
                  <TableHead>Occupancy</TableHead>
                  <TableHead>Exceptions</TableHead>
                  <TableHead className="w-[120px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} className="py-8 text-center text-muted-foreground">
                      No night audits recorded yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  runs.map((run) => (
                    <TableRow key={run.id}>
                      <TableCell className="font-medium">{run.business_date}</TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            run.status === "completed"
                              ? "default"
                              : run.status === "failed"
                                ? "destructive"
                                : run.status === "completed_with_exceptions"
                                  ? "secondary"
                                  : "secondary"
                          }
                        >
                          {run.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {run.triggered_by || "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {formatWhen(run.completed_at || run.started_at)}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {run.kpis?.room_revenue != null
                          ? formatMoney(run.kpis.room_revenue, currency)
                          : "—"}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {run.kpis?.occupancy_rate_pct != null
                          ? `${run.kpis.occupancy_rate_pct}%`
                          : "—"}
                      </TableCell>
                      <TableCell className="tabular-nums">
                        {run.exception_count ?? 0}
                      </TableCell>
                      <TableCell>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={!run.has_pdf || downloadingId === run.id}
                          onClick={() => handleDownload(run)}
                        >
                          {downloadingId === run.id ? (
                            <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Download className="mr-1 h-3.5 w-3.5" />
                          )}
                          PDF
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </ReportPageShell>
  );
}
