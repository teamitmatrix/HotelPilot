"use client";

import { useState } from "react";
import Link from "next/link";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/toast-provider";
import {
  downloadReportExcel,
  downloadReportPdf,
  type ReportExportDoc,
} from "@/lib/report-export";
import { ArrowLeft, FileSpreadsheet, FileText, Loader2 } from "lucide-react";

export function monthStartIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function ReportsBackLink({ href = "/app/hotel/reports" }: { href?: string }) {
  return (
    <Button variant="ghost" size="sm" className="-ml-2 h-8 px-2" asChild>
      <Link href={href}>
        <ArrowLeft className="mr-1 h-4 w-4" />
        All reports
      </Link>
    </Button>
  );
}

export function ReportPageShell({
  title,
  subtitle,
  backHref = "/app/hotel/reports",
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  backHref?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">{title}</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>
      <div className="flex-1 space-y-6 p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <ReportsBackLink href={backHref} />
            <div>
              <h2 className="text-2xl font-bold tracking-tight">{title}</h2>
              {subtitle ? (
                <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
              ) : null}
            </div>
          </div>
          {actions}
        </div>
        {children}
      </div>
    </div>
  );
}

export function ReportDateRange({
  fromDate,
  toDate,
  onFromChange,
  onToChange,
}: {
  fromDate: string;
  toDate: string;
  onFromChange: (value: string) => void;
  onToChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      <div>
        <Label htmlFor="report-from" className="text-xs text-muted-foreground">
          From
        </Label>
        <Input
          id="report-from"
          type="date"
          className="w-[160px]"
          value={fromDate}
          onChange={(e) => onFromChange(e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="report-to" className="text-xs text-muted-foreground">
          To
        </Label>
        <Input
          id="report-to"
          type="date"
          className="w-[160px]"
          value={toDate}
          onChange={(e) => onToChange(e.target.value)}
        />
      </div>
    </div>
  );
}

export function ReportExportButtons({
  disabled,
  filename,
  buildDoc,
}: {
  disabled?: boolean;
  filename: string;
  buildDoc: () => ReportExportDoc | null;
}) {
  const { showToast } = useToast();
  const [busy, setBusy] = useState<"excel" | "pdf" | null>(null);

  async function onExport(kind: "excel" | "pdf") {
    setBusy(kind);
    try {
      const doc = buildDoc();
      if (!doc) {
        showToast({ type: "error", message: "Nothing to export yet" });
        return;
      }
      if (kind === "excel") downloadReportExcel(doc, filename);
      else downloadReportPdf(doc, filename);
      showToast({
        type: "success",
        message: `${kind === "excel" ? "Excel" : "PDF"} export downloaded`,
      });
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Export failed",
      });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-wrap gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled || !!busy}
        onClick={() => onExport("excel")}
      >
        {busy === "excel" ? (
          <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
        ) : (
          <FileSpreadsheet className="mr-1.5 h-4 w-4" />
        )}
        Excel
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled || !!busy}
        onClick={() => onExport("pdf")}
      >
        {busy === "pdf" ? (
          <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
        ) : (
          <FileText className="mr-1.5 h-4 w-4" />
        )}
        PDF
      </Button>
    </div>
  );
}
