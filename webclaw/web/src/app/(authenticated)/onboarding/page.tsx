"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { listOnboardingPipelines } from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

function statusBadge(status: string) {
  if (status === "live") return <Badge>Live</Badge>;
  if (status === "ready") return <Badge className="bg-emerald-600 hover:bg-emerald-600">Ready</Badge>;
  if (status === "blocked") return <Badge variant="destructive">Blocked</Badge>;
  return <Badge variant="secondary">{status.replace(/_/g, " ")}</Badge>;
}

export default function OnboardingPage() {
  const [statusFilter, setStatusFilter] = useState("all");
  const { data, isLoading, error } = useQuery({
    queryKey: ["onboarding-pipelines", statusFilter],
    queryFn: () => listOnboardingPipelines(statusFilter),
  });

  useEffect(() => {
    document.title = "Onboarding Pipeline — HotelPilot Central Admin";
  }, []);

  const pipelines = data?.pipelines || [];

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 items-center gap-3 border-b px-4">
        <SidebarTrigger />
        <div className="flex-1">
          <h2 className="text-2xl font-bold tracking-tight">Onboarding pipeline</h2>
          <p className="text-sm text-muted-foreground">
            Checklist progress and go-live readiness across hotels.
          </p>
        </div>
        <ConnectionStatus />
        <ThemeToggle />
        <ChatToggleButton />
      </header>

      <div className="flex-1 space-y-4 p-4">
        {error && (
          <p className="text-sm text-destructive">{(error as Error).message}</p>
        )}

        <div className="grid gap-4 md:grid-cols-2">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                In progress / ready
              </CardTitle>
            </CardHeader>
            <CardContent className="text-2xl font-bold">
              {data?.in_progress_count ?? 0}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Showing
              </CardTitle>
            </CardHeader>
            <CardContent className="text-2xl font-bold">{pipelines.length}</CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <ListChecks className="h-4 w-4" />
              Pipelines
            </CardTitle>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All</SelectItem>
                <SelectItem value="in_progress">In progress</SelectItem>
                <SelectItem value="ready">Ready</SelectItem>
                <SelectItem value="live">Live</SelectItem>
                <SelectItem value="blocked">Blocked</SelectItem>
                <SelectItem value="abandoned">Abandoned</SelectItem>
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Hotel</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Progress</TableHead>
                    <TableHead>Invite</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pipelines.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-muted-foreground">
                        No onboarding pipelines yet
                      </TableCell>
                    </TableRow>
                  ) : (
                    pipelines.map((p) => (
                      <TableRow key={p.tenant_id}>
                        <TableCell className="font-medium">
                          {p.hotel_name || p.tenant_id}
                        </TableCell>
                        <TableCell>{p.source || "—"}</TableCell>
                        <TableCell>
                          {p.progress
                            ? `${p.progress.required_done}/${p.progress.required_total} req`
                            : "—"}
                        </TableCell>
                        <TableCell>{p.owner_invite_status || "—"}</TableCell>
                        <TableCell>{statusBadge(p.status)}</TableCell>
                        <TableCell>
                          <Button variant="outline" size="sm" asChild>
                            <Link href={`/properties/${p.tenant_id}`}>Open</Link>
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
