"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Handshake, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SidebarTrigger } from "@/components/ui/sidebar";
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
  createPartner,
  linkPartnerTenant,
  listPartners,
  listTenants,
  provisionPartnerUser,
  unlinkPartnerTenant,
  updatePartner,
  type Partner,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

export default function PartnersPage() {
  const queryClient = useQueryClient();
  const { data: partners = [], isLoading } = useQuery({
    queryKey: ["partners"],
    queryFn: listPartners,
  });
  const { data: tenants = [] } = useQuery({
    queryKey: ["platform-tenants"],
    queryFn: listTenants,
  });

  const [form, setForm] = useState({
    name: "",
    contact_email: "",
    commission_percent: "10",
    notes: "",
  });
  const [selected, setSelected] = useState<Partner | null>(null);
  const [linkTenantId, setLinkTenantId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    document.title = "Partners — HotelPilot Central Admin";
  }, []);

  useEffect(() => {
    if (selected) {
      const fresh = partners.find((p) => p.id === selected.id);
      if (fresh) setSelected(fresh);
    }
  }, [partners, selected]);

  async function create() {
    if (!form.name.trim() || !form.contact_email.trim()) {
      setMessage("Name and email required");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      await createPartner({
        name: form.name.trim(),
        contact_email: form.contact_email.trim(),
        commission_percent: Number(form.commission_percent) || 0,
        notes: form.notes,
      });
      setForm({ name: "", contact_email: "", commission_percent: "10", notes: "" });
      await queryClient.invalidateQueries({ queryKey: ["partners"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Create failed");
    } finally {
      setBusy(false);
    }
  }

  async function toggleStatus(p: Partner) {
    setBusy(true);
    try {
      await updatePartner(p.id, {
        status: p.status === "active" ? "inactive" : "active",
      });
      await queryClient.invalidateQueries({ queryKey: ["partners"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  async function link() {
    if (!selected || !linkTenantId) return;
    setBusy(true);
    try {
      await linkPartnerTenant(selected.id, linkTenantId);
      setLinkTenantId("");
      await queryClient.invalidateQueries({ queryKey: ["partners"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Link failed");
    } finally {
      setBusy(false);
    }
  }

  async function unlink(tenantId: string) {
    if (!selected) return;
    setBusy(true);
    try {
      await unlinkPartnerTenant(selected.id, tenantId);
      await queryClient.invalidateQueries({ queryKey: ["partners"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Unlink failed");
    } finally {
      setBusy(false);
    }
  }

  async function provisionConsole() {
    if (!selected) return;
    setBusy(true);
    setMessage("");
    try {
      const res = await provisionPartnerUser(selected.id);
      setMessage(`Console user ready for ${res.email} (id ${res.user_id.slice(0, 8)}…)`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Provision failed");
    } finally {
      setBusy(false);
    }
  }

  const tenantName = (id: string) =>
    tenants.find((t) => String(t.id) === id)?.hotel_name || id;

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 items-center gap-3 border-b px-4">
        <SidebarTrigger />
        <div className="flex-1">
          <h2 className="text-2xl font-bold tracking-tight">Partners / resellers</h2>
          <p className="text-sm text-muted-foreground">
            Partner accounts, commission, and linked hotels.
          </p>
        </div>
        <ConnectionStatus />
        <ThemeToggle />
        <ChatToggleButton />
      </header>

      <div className="flex-1 space-y-4 p-4">
        {message && (
          <p className="text-sm text-destructive" role="alert">
            {message}
          </p>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Handshake className="h-4 w-4" />
              New partner
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 md:grid-cols-3">
              <div className="space-y-1">
                <Label>Name</Label>
                <Input
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                />
              </div>
              <div className="space-y-1">
                <Label>Contact email</Label>
                <Input
                  value={form.contact_email}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, contact_email: e.target.value }))
                  }
                />
              </div>
              <div className="space-y-1">
                <Label>Commission %</Label>
                <Input
                  value={form.commission_percent}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, commission_percent: e.target.value }))
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
            <Button disabled={busy} onClick={create}>
              <Plus className="h-4 w-4 mr-1" />
              Create partner
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Partners</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Email</TableHead>
                    <TableHead>Commission</TableHead>
                    <TableHead>Hotels</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {partners.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-muted-foreground">
                        No partners yet
                      </TableCell>
                    </TableRow>
                  ) : (
                    partners.map((p) => (
                      <TableRow
                        key={p.id}
                        className="cursor-pointer"
                        onClick={() => setSelected(p)}
                      >
                        <TableCell className="font-medium">{p.name}</TableCell>
                        <TableCell>{p.contact_email}</TableCell>
                        <TableCell>{p.commission_percent}%</TableCell>
                        <TableCell>{p.tenant_count}</TableCell>
                        <TableCell>
                          <Badge
                            variant={p.status === "active" ? "default" : "secondary"}
                          >
                            {p.status}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleStatus(p);
                            }}
                          >
                            {p.status === "active" ? "Deactivate" : "Activate"}
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

        {selected && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Linked hotels — {selected.name}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" disabled={busy} onClick={provisionConsole}>
                  Provision console login
                </Button>
                <p className="text-xs text-muted-foreground self-center">
                  Creates/links a user for {selected.contact_email} → /partner
                </p>
              </div>
              <div className="flex flex-wrap gap-2 items-end">
                <div className="min-w-[220px] space-y-1">
                  <Label>Hotel</Label>
                  <Select value={linkTenantId} onValueChange={setLinkTenantId}>
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
                <Button disabled={busy || !linkTenantId} onClick={link}>
                  Link hotel
                </Button>
              </div>
              {selected.tenant_ids.length === 0 ? (
                <p className="text-sm text-muted-foreground">No linked hotels</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Hotel</TableHead>
                      <TableHead className="w-[60px]" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {selected.tenant_ids.map((tid) => (
                      <TableRow key={tid}>
                        <TableCell>{tenantName(tid)}</TableCell>
                        <TableCell>
                          <Button
                            size="icon"
                            variant="ghost"
                            disabled={busy}
                            onClick={() => unlink(tid)}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
