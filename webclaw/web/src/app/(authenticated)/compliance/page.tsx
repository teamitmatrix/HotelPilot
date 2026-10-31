"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Scale } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
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
  createDsarRequest,
  downloadDsarExport,
  executeDsarErasure,
  getCompliancePolicy,
  listDsarRequests,
  listTenants,
  updateCompliancePolicy,
  updateDsarRequest,
  type CompliancePolicy,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

function formatTs(iso?: string | null) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function CompliancePage() {
  const queryClient = useQueryClient();
  const { data: policyData, isLoading: policyLoading } = useQuery({
    queryKey: ["compliance-policy"],
    queryFn: getCompliancePolicy,
  });
  const [statusFilter, setStatusFilter] = useState("all");
  const { data: requests = [], isLoading: dsarLoading } = useQuery({
    queryKey: ["dsar-requests", statusFilter],
    queryFn: () => listDsarRequests(statusFilter),
  });
  const { data: tenants = [] } = useQuery({
    queryKey: ["platform-tenants"],
    queryFn: listTenants,
  });

  const [policy, setPolicy] = useState<CompliancePolicy | null>(null);
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [form, setForm] = useState({
    tenant_id: "",
    request_type: "export",
    subject_email: "",
    notes: "",
  });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    document.title = "Compliance — HotelPilot Central Admin";
  }, []);

  useEffect(() => {
    if (policyData) setPolicy({ ...policyData });
  }, [policyData]);

  async function savePolicy() {
    if (!policy) return;
    setSavingPolicy(true);
    setMessage("");
    try {
      const saved = await updateCompliancePolicy(policy);
      setPolicy(saved);
      await queryClient.invalidateQueries({ queryKey: ["compliance-policy"] });
      setMessage("Policy saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSavingPolicy(false);
    }
  }

  async function createDsar() {
    if (!form.tenant_id || !form.subject_email) {
      setMessage("Hotel and subject email are required");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const hotel = tenants.find((t) => String(t.id) === form.tenant_id);
      await createDsarRequest({
        ...form,
        hotel_name: hotel?.hotel_name,
      });
      setForm({ tenant_id: "", request_type: "export", subject_email: "", notes: "" });
      await queryClient.invalidateQueries({ queryKey: ["dsar-requests"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Create failed");
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(id: string, status: string) {
    setBusy(true);
    try {
      await updateDsarRequest(id, { status });
      await queryClient.invalidateQueries({ queryKey: ["dsar-requests"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  async function runErasure(id: string) {
    if (!window.confirm("Permanently erase/anonymize subject data for this DSAR?")) {
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      await executeDsarErasure(id);
      await queryClient.invalidateQueries({ queryKey: ["dsar-requests"] });
      setMessage("Erasure completed");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Erasure failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 items-center gap-3 border-b px-4">
        <SidebarTrigger />
        <div className="flex-1">
          <h2 className="text-2xl font-bold tracking-tight">Compliance</h2>
          <p className="text-sm text-muted-foreground">
            Retention policy and DSAR export / erasure workflow.
          </p>
        </div>
        <ConnectionStatus />
        <ThemeToggle />
        <ChatToggleButton />
      </header>

      <div className="flex-1 space-y-4 p-4">
        {message && (
          <p className="text-sm text-muted-foreground" role="status">
            {message}
          </p>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Scale className="h-4 w-4" />
              Retention policy
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {policyLoading || !policy ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <>
                <div className="grid gap-3 md:grid-cols-3">
                  <div className="space-y-1">
                    <Label>Retention days</Label>
                    <Input
                      type="number"
                      value={policy.retention_days}
                      onChange={(e) =>
                        setPolicy({
                          ...policy,
                          retention_days: Number(e.target.value) || 365,
                        })
                      }
                    />
                  </div>
                  <div className="space-y-1">
                    <Label>DPA contact email</Label>
                    <Input
                      value={policy.dpa_contact_email}
                      onChange={(e) =>
                        setPolicy({ ...policy, dpa_contact_email: e.target.value })
                      }
                    />
                  </div>
                  <div className="flex items-end gap-2 pb-2">
                    <Switch
                      checked={policy.auto_purge_cancelled}
                      onCheckedChange={(v) =>
                        setPolicy({ ...policy, auto_purge_cancelled: v })
                      }
                    />
                    <Label>Auto-purge cancelled tenants</Label>
                  </div>
                </div>
                <Button disabled={savingPolicy} onClick={savePolicy}>
                  Save policy
                </Button>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">New DSAR request</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 md:grid-cols-3">
              <div className="space-y-1">
                <Label>Hotel</Label>
                <Select
                  value={form.tenant_id}
                  onValueChange={(v) => setForm((f) => ({ ...f, tenant_id: v }))}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select hotel" />
                  </SelectTrigger>
                  <SelectContent>
                    {tenants.map((t) => (
                      <SelectItem key={String(t.id)} value={String(t.id)}>
                        {t.hotel_name || t.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Type</Label>
                <Select
                  value={form.request_type}
                  onValueChange={(v) => setForm((f) => ({ ...f, request_type: v }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="export">Export</SelectItem>
                    <SelectItem value="erasure">Erasure</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Subject email</Label>
                <Input
                  value={form.subject_email}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, subject_email: e.target.value }))
                  }
                />
              </div>
            </div>
            <div className="space-y-1">
              <Label>Notes</Label>
              <Textarea
                value={form.notes}
                onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              />
            </div>
            <Button disabled={busy} onClick={createDsar}>
              Create request
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">DSAR queue</CardTitle>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All</SelectItem>
                <SelectItem value="open">Open</SelectItem>
                <SelectItem value="in_progress">In progress</SelectItem>
                <SelectItem value="completed">Completed</SelectItem>
                <SelectItem value="rejected">Rejected</SelectItem>
              </SelectContent>
            </Select>
          </CardHeader>
          <CardContent>
            {dsarLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Subject</TableHead>
                    <TableHead>Hotel</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Updated</TableHead>
                    <TableHead className="w-[220px]">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {requests.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-muted-foreground">
                        No requests
                      </TableCell>
                    </TableRow>
                  ) : (
                    requests.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell>{r.subject_email}</TableCell>
                        <TableCell>{r.hotel_name || r.tenant_id}</TableCell>
                        <TableCell>
                          <Badge variant="outline">{r.request_type}</Badge>
                        </TableCell>
                        <TableCell>{r.status}</TableCell>
                        <TableCell>{formatTs(r.updated_at)}</TableCell>
                        <TableCell className="space-x-1">
                          {r.status === "open" && (
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              onClick={() => setStatus(r.id, "in_progress")}
                            >
                              Start
                            </Button>
                          )}
                          {r.status !== "completed" &&
                            r.status !== "rejected" &&
                            r.request_type !== "erasure" && (
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              onClick={() => setStatus(r.id, "completed")}
                            >
                              Complete
                            </Button>
                          )}
                          {r.request_type === "export" && (
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={busy}
                              onClick={() => downloadDsarExport(r.id)}
                            >
                              <Download className="h-4 w-4" />
                            </Button>
                          )}
                          {r.request_type === "erasure" &&
                            r.status !== "completed" &&
                            r.status !== "rejected" && (
                              <Button
                                size="sm"
                                variant="destructive"
                                disabled={busy}
                                onClick={() => runErasure(r.id)}
                              >
                                Execute erasure
                              </Button>
                            )}
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
