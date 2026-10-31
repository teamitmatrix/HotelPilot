"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  DatabaseBackup,
  Download,
  HardDrive,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  Shield,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ackOpsAlert,
  createBackupSnapshot,
  downloadBackup,
  getOpsHealth,
  getSecurityPolicy,
  listBackups,
  listOpsAlerts,
  restoreBackup,
  setOpsJobPaused,
  updateSecurityPolicy,
  type SecurityPolicy,
} from "@/lib/admin-api";
import { Textarea } from "@/components/ui/textarea";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

function formatBytes(n: number) {
  if (!n || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

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

export default function SecurityPage() {
  const queryClient = useQueryClient();
  const { data: policyData, isLoading: policyLoading } = useQuery({
    queryKey: ["security-policy"],
    queryFn: getSecurityPolicy,
  });
  const { data: backups = [], isLoading: backupsLoading } = useQuery({
    queryKey: ["platform-backups"],
    queryFn: listBackups,
  });
  const { data: opsHealth } = useQuery({
    queryKey: ["ops-health"],
    queryFn: getOpsHealth,
    refetchInterval: 30_000,
  });
  const { data: opsAlerts = [] } = useQuery({
    queryKey: ["ops-alerts"],
    queryFn: () => listOpsAlerts(false),
    refetchInterval: 30_000,
  });

  const [policy, setPolicy] = useState<SecurityPolicy | null>(null);
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const [downloadId, setDownloadId] = useState<string | null>(null);
  const [restoreId, setRestoreId] = useState<string | null>(null);
  const [jobBusy, setJobBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    document.title = "Security & Backups — HotelPilot Central Admin";
  }, []);

  useEffect(() => {
    if (policyData) setPolicy({ ...policyData });
  }, [policyData]);

  async function savePolicy() {
    if (!policy) return;
    setSavingPolicy(true);
    setMessage("");
    try {
      const saved = await updateSecurityPolicy(policy);
      setPolicy(saved);
      await queryClient.invalidateQueries({ queryKey: ["security-policy"] });
      setMessage("Security policy saved.");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setSavingPolicy(false);
    }
  }

  async function runSnapshot() {
    setSnapshotBusy(true);
    setMessage("");
    try {
      await createBackupSnapshot();
      await queryClient.invalidateQueries({ queryKey: ["platform-backups"] });
      setMessage("Database snapshot completed.");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setSnapshotBusy(false);
    }
  }

  async function toggleJob(jobKey: string, pause: boolean) {
    setJobBusy(jobKey);
    setMessage("");
    try {
      await setOpsJobPaused(jobKey, pause);
      await queryClient.invalidateQueries({ queryKey: ["ops-health"] });
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setJobBusy(null);
    }
  }

  async function ackAlert(id: string) {
    try {
      await ackOpsAlert(id);
      await queryClient.invalidateQueries({ queryKey: ["ops-alerts"] });
      await queryClient.invalidateQueries({ queryKey: ["ops-health"] });
    } catch (e) {
      setMessage((e as Error).message);
    }
  }

  async function handleDownload(id: string, filename: string) {
    setDownloadId(id);
    setMessage("");
    try {
      await downloadBackup(id, filename);
      await queryClient.invalidateQueries({ queryKey: ["platform-backups"] });
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setDownloadId(null);
    }
  }

  async function handleRestore(id: string, filename: string) {
    const ok = window.confirm(
      `Restore ${filename} into the live database?\n\nThis overwrites the webclaw schema. Type OK only if you have a recent snapshot.`,
    );
    if (!ok) return;
    setRestoreId(id);
    setMessage("");
    try {
      const msg = await restoreBackup(id);
      setMessage(msg);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setRestoreId(null);
    }
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Security & Backups</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6 max-w-5xl">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">System security</h2>
          <p className="text-muted-foreground">
            Global auth policies and on-demand database snapshots for HotelPilot Central Admin.
          </p>
        </div>

        {message ? (
          <p
            className={`text-sm ${
              message.toLowerCase().includes("fail") ||
              message.toLowerCase().includes("error")
                ? "text-destructive"
                : "text-muted-foreground"
            }`}
          >
            {message}
          </p>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Shield className="h-4 w-4" />
              Security policy
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-6">
            {policyLoading || !policy ? (
              <p className="text-sm text-muted-foreground">Loading policy…</p>
            ) : (
              <>
                <div className="flex items-center justify-between gap-4 rounded-lg border px-4 py-3">
                  <div>
                    <p className="text-sm font-medium">Enforce 2FA</p>
                    <p className="text-xs text-muted-foreground">
                      Require two-factor authentication for all platform and hotel admin logins.
                    </p>
                  </div>
                  <Switch
                    checked={policy.enforce_2fa}
                    onCheckedChange={(v) =>
                      setPolicy((p) => (p ? { ...p, enforce_2fa: v } : p))
                    }
                  />
                </div>
                <div className="space-y-3 rounded-lg border px-4 py-3">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium">Maintenance mode</p>
                      <p className="text-xs text-muted-foreground">
                        Blocks hotel staff logins. Platform operators can still sign in.
                      </p>
                    </div>
                    <Switch
                      checked={Boolean(policy.maintenance_mode)}
                      onCheckedChange={(v) =>
                        setPolicy((p) => (p ? { ...p, maintenance_mode: v } : p))
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="maint-msg">Maintenance message</Label>
                    <Textarea
                      id="maint-msg"
                      rows={2}
                      value={policy.maintenance_message || ""}
                      onChange={(e) =>
                        setPolicy((p) =>
                          p ? { ...p, maintenance_message: e.target.value } : p,
                        )
                      }
                    />
                  </div>
                </div>
                <div className="space-y-3 rounded-lg border px-4 py-3">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium">Automated backups</p>
                      <p className="text-xs text-muted-foreground">
                        Scheduler runs pg_dump on an interval when enabled.
                        {policy.last_auto_backup_at
                          ? ` Last auto: ${formatTs(policy.last_auto_backup_at)}.`
                          : ""}
                      </p>
                    </div>
                    <Switch
                      checked={Boolean(policy.auto_backup_enabled)}
                      onCheckedChange={(v) =>
                        setPolicy((p) => (p ? { ...p, auto_backup_enabled: v } : p))
                      }
                    />
                  </div>
                  <div className="max-w-xs space-y-1.5">
                    <Label htmlFor="backup-interval">Interval (hours)</Label>
                    <Input
                      id="backup-interval"
                      type="number"
                      min={1}
                      max={168}
                      value={policy.auto_backup_interval_hours ?? 24}
                      onChange={(e) =>
                        setPolicy((p) =>
                          p
                            ? {
                                ...p,
                                auto_backup_interval_hours: Number(e.target.value) || 24,
                              }
                            : p,
                        )
                      }
                    />
                  </div>
                </div>
                <div className="space-y-3 rounded-lg border px-4 py-3">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium">IP allowlist</p>
                      <p className="text-xs text-muted-foreground">
                        When enabled, only listed IPs/CIDRs can sign in. Login anomalies
                        still raise ops alerts on the console below.
                      </p>
                    </div>
                    <Switch
                      checked={Boolean(policy.ip_allowlist_enabled)}
                      onCheckedChange={(v) =>
                        setPolicy((p) => (p ? { ...p, ip_allowlist_enabled: v } : p))
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="ip-allowlist">Allowed IPs (one per line)</Label>
                    <Textarea
                      id="ip-allowlist"
                      rows={3}
                      placeholder={"127.0.0.1\n10.0.0.0/8"}
                      value={(policy.ip_allowlist || []).join("\n")}
                      onChange={(e) =>
                        setPolicy((p) =>
                          p
                            ? {
                                ...p,
                                ip_allowlist: e.target.value
                                  .split(/\r?\n/)
                                  .map((s) => s.trim())
                                  .filter(Boolean),
                              }
                            : p,
                        )
                      }
                    />
                  </div>
                </div>
                <div className="space-y-3 rounded-lg border px-4 py-3">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium">Offsite backup copy</p>
                      <p className="text-xs text-muted-foreground">
                        After each successful dump, PUT the file to a remote URL (S3
                        pre-signed, webhook, or object store endpoint).
                      </p>
                    </div>
                    <Switch
                      checked={Boolean(policy.offsite_backup_enabled)}
                      onCheckedChange={(v) =>
                        setPolicy((p) => (p ? { ...p, offsite_backup_enabled: v } : p))
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="offsite-url">Offsite URL</Label>
                    <Input
                      id="offsite-url"
                      placeholder="https://…"
                      value={policy.offsite_backup_url || ""}
                      onChange={(e) =>
                        setPolicy((p) =>
                          p ? { ...p, offsite_backup_url: e.target.value } : p,
                        )
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="offsite-token">
                      Bearer token
                      {policy.offsite_backup_token_set
                        ? " (saved — leave blank to keep)"
                        : ""}
                    </Label>
                    <Input
                      id="offsite-token"
                      type="password"
                      autoComplete="off"
                      placeholder={
                        policy.offsite_backup_token_set ? "••••••••" : "Optional"
                      }
                      value={policy.offsite_backup_token || ""}
                      onChange={(e) =>
                        setPolicy((p) =>
                          p ? { ...p, offsite_backup_token: e.target.value } : p,
                        )
                      }
                    />
                  </div>
                </div>
                <div className="grid gap-4 sm:grid-cols-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="session-timeout">Session timeout (minutes)</Label>
                    <Input
                      id="session-timeout"
                      type="number"
                      min={5}
                      max={1440}
                      value={policy.session_timeout_minutes}
                      onChange={(e) =>
                        setPolicy((p) =>
                          p
                            ? {
                                ...p,
                                session_timeout_minutes: Number(e.target.value) || 15,
                              }
                            : p,
                        )
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="pwd-min">Password min length</Label>
                    <Input
                      id="pwd-min"
                      type="number"
                      min={6}
                      max={128}
                      value={policy.password_min_length}
                      onChange={(e) =>
                        setPolicy((p) =>
                          p
                            ? {
                                ...p,
                                password_min_length: Number(e.target.value) || 8,
                              }
                            : p,
                        )
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="lockout">Lockout after failures</Label>
                    <Input
                      id="lockout"
                      type="number"
                      min={1}
                      max={50}
                      value={policy.lockout_after_failures}
                      onChange={(e) =>
                        setPolicy((p) =>
                          p
                            ? {
                                ...p,
                                lockout_after_failures: Number(e.target.value) || 5,
                              }
                            : p,
                        )
                      }
                    />
                  </div>
                </div>
                <Button onClick={savePolicy} disabled={savingPolicy}>
                  {savingPolicy ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : null}
                  Save policy
                </Button>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4" />
              System health & job controls
            </CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Pause background schedulers and acknowledge ops alerts.
              {opsHealth
                ? ` Status: ${opsHealth.status} · open alerts: ${opsHealth.open_alerts}.`
                : ""}
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              {(opsHealth?.jobs || []).map((job) => (
                <div
                  key={job.job_key}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2"
                >
                  <div>
                    <p className="text-sm font-medium">{job.label}</p>
                    <p className="text-xs text-muted-foreground">
                      {job.description}
                      {job.last_run_at
                        ? ` · last ${formatTs(job.last_run_at)} (${job.last_status || "—"})`
                        : ""}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {job.is_paused ? (
                      <Badge variant="destructive">Paused</Badge>
                    ) : (
                      <Badge variant="secondary">Running</Badge>
                    )}
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={jobBusy === job.job_key}
                      onClick={() => toggleJob(job.job_key, !job.is_paused)}
                    >
                      {job.is_paused ? (
                        <Play className="h-4 w-4" />
                      ) : (
                        <Pause className="h-4 w-4" />
                      )}
                      {job.is_paused ? "Resume" : "Pause"}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
            {opsAlerts.length > 0 && (
              <div>
                <p className="mb-2 text-sm font-medium">Open alerts</p>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>When</TableHead>
                      <TableHead>Severity</TableHead>
                      <TableHead>Title</TableHead>
                      <TableHead className="w-[80px]" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {opsAlerts.map((a) => (
                      <TableRow key={a.id}>
                        <TableCell className="text-xs">{formatTs(a.created_at)}</TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              a.severity === "critical" ? "destructive" : "secondary"
                            }
                          >
                            {a.severity}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-sm">{a.title}</TableCell>
                        <TableCell>
                          <Button size="sm" variant="ghost" onClick={() => ackAlert(a.id)}>
                            Ack
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <HardDrive className="h-4 w-4" />
                Database backups
              </CardTitle>
              <p className="mt-1 text-xs text-muted-foreground">
                Trigger a PostgreSQL dump (pg_dump of the webclaw schema), download prior backups, or restore with confirmation. Restore requires psql on the API host and overwrites the live schema.
              </p>
            </div>
            <Button onClick={runSnapshot} disabled={snapshotBusy}>
              {snapshotBusy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <DatabaseBackup className="h-4 w-4" />
              )}
              Run DB snapshot
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Created</TableHead>
                  <TableHead>Filename</TableHead>
                  <TableHead>Size</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Downloaded</TableHead>
                  <TableHead className="w-[90px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {backupsLoading ? (
                  <TableRow>
                    <TableCell colSpan={6} className="py-12 text-center text-muted-foreground">
                      Loading backups…
                    </TableCell>
                  </TableRow>
                ) : backups.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="py-12 text-center text-muted-foreground">
                      No snapshots yet. Run a database snapshot to create the first backup.
                    </TableCell>
                  </TableRow>
                ) : (
                  backups.map((b) => (
                    <TableRow key={b.id}>
                      <TableCell className="text-xs">{formatTs(b.created_at)}</TableCell>
                      <TableCell className="font-mono text-xs">{b.filename}</TableCell>
                      <TableCell className="text-xs">{formatBytes(b.size_bytes)}</TableCell>
                      <TableCell>
                        <Badge
                          variant={b.status === "completed" ? "default" : "destructive"}
                          className="capitalize"
                        >
                          {b.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatTs(b.downloaded_at)}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            disabled={b.status !== "completed" || downloadId === b.id}
                            onClick={() => handleDownload(b.id, b.filename)}
                            aria-label="Download backup"
                          >
                            {downloadId === b.id ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Download className="h-4 w-4" />
                            )}
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            disabled={
                              b.status !== "completed" ||
                              restoreId === b.id ||
                              Boolean(restoreId)
                            }
                            onClick={() => handleRestore(b.id, b.filename)}
                            aria-label="Restore backup"
                            title="Restore (requires confirm)"
                          >
                            {restoreId === b.id ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <RotateCcw className="h-4 w-4" />
                            )}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
