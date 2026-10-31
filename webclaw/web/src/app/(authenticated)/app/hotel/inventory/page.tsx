"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { HotelOpsHeader } from "@/components/hotel/hotel-ops-header";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useEntityList, useEntityMutation } from "@/lib/hooks";
import { useToast } from "@/components/toast-provider";
import {
  SKILL,
  type Hotel,
  type HotelRoom,
  type RatePlan,
  type RoomType,
  type TaxRule,
  ROOM_STATUS_CLASS,
  ROOM_STATUS_LABEL,
  formatMoney,
  hotelCurrency,
  itemsFrom,
} from "@/lib/hospitality";
import { Loader2, Pencil, Plus, Trash2 } from "lucide-react";

function activeFlag(value: unknown): boolean {
  return value === 1 || value === true || value === "1";
}

export default function HotelInventorySettingsPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const mutate = useEntityMutation(SKILL);
  const canEdit = perms.canManageRooms;

  const hotelsQ = useEntityList(SKILL, "list-hotels", { limit: "20" }, { enabled: perms.canManageRooms });
  const typesQ = useEntityList(SKILL, "list-room-types", { limit: "200" }, { enabled: perms.canManageRooms });
  const roomsQ = useEntityList(SKILL, "list-rooms", { limit: "500" }, { enabled: perms.canManageRooms });
  const ratesQ = useEntityList(SKILL, "list-rate-plans", { limit: "200" }, { enabled: perms.canManageRooms });
  const taxesQ = useEntityList(SKILL, "list-tax-rules", { limit: "200" }, { enabled: perms.canManageRooms });

  const hotels = itemsFrom<Hotel>(hotelsQ.data, "hotels");
  const types = itemsFrom<RoomType>(typesQ.data, "room_types");
  const rooms = itemsFrom<HotelRoom>(roomsQ.data, "rooms");
  const ratePlans = itemsFrom<RatePlan>(ratesQ.data, "rate_plans");
  const taxRules = itemsFrom<TaxRule>(taxesQ.data, "tax_rules");
  const hotelId = hotels[0]?.id ?? "";
  const currency = hotelCurrency(hotels[0]?.currency);

  const [typeOpen, setTypeOpen] = useState(false);
  const [roomOpen, setRoomOpen] = useState(false);
  const [rateOpen, setRateOpen] = useState(false);
  const [taxOpen, setTaxOpen] = useState(false);
  const [editingType, setEditingType] = useState<RoomType | null>(null);
  const [editingRoom, setEditingRoom] = useState<HotelRoom | null>(null);
  const [editingRate, setEditingRate] = useState<RatePlan | null>(null);
  const [editingTax, setEditingTax] = useState<TaxRule | null>(null);

  const [typeForm, setTypeForm] = useState({
    name: "",
    code: "",
    base_rate: "",
    max_occupancy: "2",
    description: "",
  });
  const [roomForm, setRoomForm] = useState({
    room_number: "",
    room_type_id: "",
    floor: "",
  });
  const [rateForm, setRateForm] = useState({
    name: "",
    code: "",
    room_type_id: "",
    start_date: "",
    end_date: "",
    nightly_rate: "",
    priority: "10",
    notes: "",
    is_active: true,
  });
  const [taxForm, setTaxForm] = useState({
    name: "",
    rate_percent: "10",
    applies_to: "room",
    tax_template_id: "",
    is_default: false,
    is_active: true,
  });

  const typeName = useMemo(() => {
    const map = new Map(types.map((t) => [t.id, t.name]));
    return (id?: string | null) => (id ? map.get(id) || "—" : "All types");
  }, [types]);

  function openNewType() {
    setEditingType(null);
    setTypeForm({ name: "", code: "", base_rate: "", max_occupancy: "2", description: "" });
    setTypeOpen(true);
  }
  function openEditType(rt: RoomType) {
    setEditingType(rt);
    setTypeForm({
      name: rt.name,
      code: rt.code,
      base_rate: String(rt.base_rate ?? ""),
      max_occupancy: String(rt.max_occupancy ?? 2),
      description: rt.description ?? "",
    });
    setTypeOpen(true);
  }
  function openNewRoom() {
    setEditingRoom(null);
    setRoomForm({ room_number: "", room_type_id: types[0]?.id ?? "", floor: "" });
    setRoomOpen(true);
  }
  function openEditRoom(room: HotelRoom) {
    setEditingRoom(room);
    setRoomForm({
      room_number: room.room_number,
      room_type_id: room.room_type_id,
      floor: room.floor ?? "",
    });
    setRoomOpen(true);
  }
  function openNewRate() {
    setEditingRate(null);
    const today = new Date().toISOString().slice(0, 10);
    setRateForm({
      name: "",
      code: "",
      room_type_id: "",
      start_date: today,
      end_date: today,
      nightly_rate: types[0] ? String(types[0].base_rate) : "",
      priority: "10",
      notes: "",
      is_active: true,
    });
    setRateOpen(true);
  }
  function openEditRate(plan: RatePlan) {
    setEditingRate(plan);
    setRateForm({
      name: plan.name,
      code: plan.code,
      room_type_id: plan.room_type_id || "",
      start_date: plan.start_date,
      end_date: plan.end_date,
      nightly_rate: String(plan.nightly_rate ?? ""),
      priority: String(plan.priority ?? 0),
      notes: plan.notes ?? "",
      is_active: activeFlag(plan.is_active),
    });
    setRateOpen(true);
  }
  function openNewTax() {
    setEditingTax(null);
    setTaxForm({
      name: "Occupancy Tax",
      rate_percent: "10",
      applies_to: "room",
      tax_template_id: "",
      is_default: true,
      is_active: true,
    });
    setTaxOpen(true);
  }
  function openEditTax(rule: TaxRule) {
    setEditingTax(rule);
    setTaxForm({
      name: rule.name,
      rate_percent: String(rule.rate_percent ?? ""),
      applies_to: rule.applies_to || "room",
      tax_template_id: rule.erp_tax_template_id || "",
      is_default: activeFlag(rule.is_default),
      is_active: activeFlag(rule.is_active),
    });
    setTaxOpen(true);
  }

  async function saveType() {
    try {
      if (editingType) {
        await mutate.mutateAsync({ action: "update-room-type", body: { id: editingType.id, ...typeForm } });
        showToast({ type: "success", message: "Room type updated" });
      } else {
        await mutate.mutateAsync({
          action: "add-room-type",
          body: { hotel_id: hotelId, ...typeForm },
        });
        showToast({ type: "success", message: "Room type created" });
      }
      setTypeOpen(false);
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Save failed" });
    }
  }

  async function saveRoom() {
    try {
      if (editingRoom) {
        await mutate.mutateAsync({ action: "update-room", body: { id: editingRoom.id, ...roomForm } });
        showToast({ type: "success", message: "Room updated" });
      } else {
        await mutate.mutateAsync({
          action: "add-room",
          body: { hotel_id: hotelId, ...roomForm },
        });
        showToast({ type: "success", message: "Room created" });
      }
      setRoomOpen(false);
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Save failed" });
    }
  }

  async function saveRate() {
    try {
      const body = {
        hotel_id: hotelId,
        name: rateForm.name,
        code: rateForm.code,
        room_type_id: rateForm.room_type_id || "",
        start_date: rateForm.start_date,
        end_date: rateForm.end_date,
        nightly_rate: rateForm.nightly_rate,
        priority: rateForm.priority,
        notes: rateForm.notes || undefined,
        is_active: rateForm.is_active ? "1" : "0",
      };
      if (editingRate) {
        await mutate.mutateAsync({ action: "update-rate-plan", body: { id: editingRate.id, ...body } });
        showToast({ type: "success", message: "Rate plan updated" });
      } else {
        await mutate.mutateAsync({ action: "add-rate-plan", body });
        showToast({ type: "success", message: "Rate plan created" });
      }
      setRateOpen(false);
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Save failed" });
    }
  }

  async function saveTax() {
    try {
      const body = {
        hotel_id: hotelId,
        name: taxForm.name,
        rate_percent: taxForm.rate_percent,
        applies_to: taxForm.applies_to,
        tax_template_id: taxForm.tax_template_id || undefined,
        is_default: taxForm.is_default ? "1" : "0",
        is_active: taxForm.is_active ? "1" : "0",
      };
      if (editingTax) {
        await mutate.mutateAsync({ action: "update-tax-rule", body: { id: editingTax.id, ...body } });
        showToast({ type: "success", message: "Tax rule updated" });
      } else {
        await mutate.mutateAsync({ action: "add-tax-rule", body });
        showToast({ type: "success", message: "Tax rule created" });
      }
      setTaxOpen(false);
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Save failed" });
    }
  }

  async function remove(action: string, id: string, label: string) {
    if (!confirm(`Delete ${label}?`)) return;
    try {
      await mutate.mutateAsync({ action, body: { id } });
      showToast({ type: "success", message: `${label} deleted` });
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Delete failed" });
    }
  }

  if (!perms.canManageRooms) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        You do not have permission to manage inventory or rates.
      </div>
    );
  }

  const loading =
    typesQ.isLoading || roomsQ.isLoading || ratesQ.isLoading || taxesQ.isLoading;

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Inventory & rates</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <HotelOpsHeader
          title="Room setup & pricing"
          subtitle="Room types, room numbers, monthly / date-range rates, and tax rules"
          perms={perms}
          actions={
            canEdit ? (
              <div className="flex flex-wrap gap-2">
                <Button asChild>
                  <Link href="/app/hotel/inventory/availability">Availability grid</Link>
                </Button>
                <Button asChild variant="outline">
                  <Link href="/app/hotel/inventory/restrictions">Restrictions</Link>
                </Button>
                <Button asChild variant="outline">
                  <Link href="/app/hotel/inventory/dynamic-rates">Dynamic rates calendar</Link>
                </Button>
                <Button asChild variant="outline">
                  <Link href="/app/hotel/inventory/pricing-rules">Yield pricing rules</Link>
                </Button>
              </div>
            ) : null
          }
        />

        {loading ? (
          <div className="flex h-48 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <Tabs defaultValue="types">
            <TabsList className="flex h-auto flex-wrap">
              <TabsTrigger value="types">Room types</TabsTrigger>
              <TabsTrigger value="rooms">Room numbers</TabsTrigger>
              <TabsTrigger value="rates">Rate plans</TabsTrigger>
              <TabsTrigger value="taxes">Tax rules</TabsTrigger>
            </TabsList>

            <TabsContent value="types" className="mt-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0">
                  <CardTitle className="text-base">Room types</CardTitle>
                  {canEdit ? (
                    <Button size="sm" className="gap-1.5" onClick={openNewType}>
                      <Plus className="h-3.5 w-3.5" />
                      Add type
                    </Button>
                  ) : null}
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>Code</TableHead>
                        <TableHead>Base rate ({currency})</TableHead>
                        <TableHead>Occupancy</TableHead>
                        {canEdit ? <TableHead className="w-24" /> : null}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {types.length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={5} className="text-muted-foreground">
                            No room types yet.
                          </TableCell>
                        </TableRow>
                      ) : (
                        types.map((rt) => (
                          <TableRow key={rt.id}>
                            <TableCell className="font-medium">{rt.name}</TableCell>
                            <TableCell>{rt.code}</TableCell>
                            <TableCell>{formatMoney(rt.base_rate, currency)}</TableCell>
                            <TableCell>{rt.max_occupancy ?? 2}</TableCell>
                            {canEdit ? (
                              <TableCell className="space-x-1 text-right">
                                <Button size="icon" variant="ghost" onClick={() => openEditType(rt)}>
                                  <Pencil className="h-3.5 w-3.5" />
                                </Button>
                                <Button
                                  size="icon"
                                  variant="ghost"
                                  onClick={() => remove("delete-room-type", rt.id, rt.name)}
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </Button>
                              </TableCell>
                            ) : null}
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="rooms" className="mt-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0">
                  <CardTitle className="text-base">Room numbers</CardTitle>
                  {canEdit ? (
                    <Button size="sm" className="gap-1.5" onClick={openNewRoom}>
                      <Plus className="h-3.5 w-3.5" />
                      Add room
                    </Button>
                  ) : null}
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Number</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Floor</TableHead>
                        <TableHead>Status</TableHead>
                        {canEdit ? <TableHead className="w-24" /> : null}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {rooms.map((room) => (
                        <TableRow key={room.id}>
                          <TableCell className="font-medium">{room.room_number}</TableCell>
                          <TableCell>{room.room_type_name || typeName(room.room_type_id)}</TableCell>
                          <TableCell>{room.floor || "—"}</TableCell>
                          <TableCell>
                            <Badge className={ROOM_STATUS_CLASS[room.status]} variant="secondary">
                              {ROOM_STATUS_LABEL[room.status]}
                            </Badge>
                          </TableCell>
                          {canEdit ? (
                            <TableCell className="space-x-1 text-right">
                              <Button size="icon" variant="ghost" onClick={() => openEditRoom(room)}>
                                <Pencil className="h-3.5 w-3.5" />
                              </Button>
                              <Button
                                size="icon"
                                variant="ghost"
                                onClick={() => remove("delete-room", room.id, `room ${room.room_number}`)}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            </TableCell>
                          ) : null}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="rates" className="mt-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0">
                  <div>
                    <CardTitle className="text-base">Rate plans</CardTitle>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Monthly and date-range tariffs. Prefer the{" "}
                      <Link href="/app/hotel/inventory/dynamic-rates" className="underline underline-offset-2">
                        dynamic rates calendar
                      </Link>{" "}
                      to set months and ranges visually. Room-type plans outrank hotel-wide plans.
                    </p>
                  </div>
                  {canEdit ? (
                    <div className="flex gap-2">
                      <Button asChild size="sm" variant="outline">
                        <Link href="/app/hotel/inventory/dynamic-rates">Calendar</Link>
                      </Button>
                      <Button size="sm" className="gap-1.5" onClick={openNewRate}>
                        <Plus className="h-3.5 w-3.5" />
                        Add plan
                      </Button>
                    </div>
                  ) : null}
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>Dates</TableHead>
                        <TableHead>Room type</TableHead>
                        <TableHead>Nightly ({currency})</TableHead>
                        <TableHead>Priority</TableHead>
                        <TableHead>Status</TableHead>
                        {canEdit ? <TableHead className="w-24" /> : null}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {ratePlans.length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={7} className="text-muted-foreground">
                            No seasonal plans — room-type base rates are used.
                          </TableCell>
                        </TableRow>
                      ) : (
                        ratePlans.map((plan) => (
                          <TableRow key={plan.id}>
                            <TableCell>
                              <div className="font-medium">{plan.name}</div>
                              <div className="text-xs text-muted-foreground">{plan.code}</div>
                            </TableCell>
                            <TableCell className="text-sm">
                              {plan.start_date} → {plan.end_date}
                            </TableCell>
                            <TableCell>{plan.room_type_name || typeName(plan.room_type_id)}</TableCell>
                            <TableCell>{formatMoney(plan.nightly_rate, currency)}</TableCell>
                            <TableCell>{plan.priority ?? 0}</TableCell>
                            <TableCell>
                              <Badge variant={activeFlag(plan.is_active) ? "default" : "secondary"}>
                                {activeFlag(plan.is_active) ? "Active" : "Off"}
                              </Badge>
                            </TableCell>
                            {canEdit ? (
                              <TableCell className="space-x-1 text-right">
                                <Button size="icon" variant="ghost" onClick={() => openEditRate(plan)}>
                                  <Pencil className="h-3.5 w-3.5" />
                                </Button>
                                <Button
                                  size="icon"
                                  variant="ghost"
                                  onClick={() => remove("delete-rate-plan", plan.id, plan.name)}
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </Button>
                              </TableCell>
                            ) : null}
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="taxes" className="mt-4">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between space-y-0">
                  <div>
                    <CardTitle className="text-base">Tax rules</CardTitle>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Applied when room or F&amp;B charges post. Optional ERP tax template id links the rule to erpclaw.
                    </p>
                  </div>
                  {canEdit ? (
                    <Button size="sm" className="gap-1.5" onClick={openNewTax}>
                      <Plus className="h-3.5 w-3.5" />
                      Add tax rule
                    </Button>
                  ) : null}
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>Rate</TableHead>
                        <TableHead>Applies to</TableHead>
                        <TableHead>ERP template</TableHead>
                        <TableHead>Flags</TableHead>
                        {canEdit ? <TableHead className="w-24" /> : null}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {taxRules.length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={6} className="text-muted-foreground">
                            No tax rules configured.
                          </TableCell>
                        </TableRow>
                      ) : (
                        taxRules.map((rule) => (
                          <TableRow key={rule.id}>
                            <TableCell className="font-medium">{rule.name}</TableCell>
                            <TableCell>{rule.rate_percent}%</TableCell>
                            <TableCell className="uppercase">{rule.applies_to}</TableCell>
                            <TableCell className="max-w-[140px] truncate text-xs text-muted-foreground">
                              {rule.erp_tax_template_id || "—"}
                            </TableCell>
                            <TableCell className="space-x-1">
                              {activeFlag(rule.is_default) ? <Badge variant="secondary">Default</Badge> : null}
                              <Badge variant={activeFlag(rule.is_active) ? "default" : "outline"}>
                                {activeFlag(rule.is_active) ? "Active" : "Off"}
                              </Badge>
                            </TableCell>
                            {canEdit ? (
                              <TableCell className="space-x-1 text-right">
                                <Button size="icon" variant="ghost" onClick={() => openEditTax(rule)}>
                                  <Pencil className="h-3.5 w-3.5" />
                                </Button>
                                <Button
                                  size="icon"
                                  variant="ghost"
                                  onClick={() => remove("delete-tax-rule", rule.id, rule.name)}
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </Button>
                              </TableCell>
                            ) : null}
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        )}
      </div>

      <Dialog open={typeOpen} onOpenChange={setTypeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingType ? "Edit room type" : "Add room type"}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5 sm:col-span-2">
              <Label>Name</Label>
              <Input value={typeForm.name} onChange={(e) => setTypeForm({ ...typeForm, name: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Code</Label>
              <Input value={typeForm.code} onChange={(e) => setTypeForm({ ...typeForm, code: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Base rate ({currency})</Label>
              <Input value={typeForm.base_rate} onChange={(e) => setTypeForm({ ...typeForm, base_rate: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Max occupancy</Label>
              <Input value={typeForm.max_occupancy} onChange={(e) => setTypeForm({ ...typeForm, max_occupancy: e.target.value })} />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label>Description</Label>
              <Textarea value={typeForm.description} onChange={(e) => setTypeForm({ ...typeForm, description: e.target.value })} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTypeOpen(false)}>Cancel</Button>
            <Button onClick={saveType} disabled={mutate.isPending || !typeForm.name || !typeForm.code}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={roomOpen} onOpenChange={setRoomOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingRoom ? "Edit room" : "Add room"}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1.5">
              <Label>Room number</Label>
              <Input value={roomForm.room_number} onChange={(e) => setRoomForm({ ...roomForm, room_number: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Room type</Label>
              <Select value={roomForm.room_type_id} onValueChange={(v) => setRoomForm({ ...roomForm, room_type_id: v })}>
                <SelectTrigger className="w-full"><SelectValue placeholder="Select type" /></SelectTrigger>
                <SelectContent>
                  {types.map((rt) => (
                    <SelectItem key={rt.id} value={rt.id}>{rt.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Floor</Label>
              <Input value={roomForm.floor} onChange={(e) => setRoomForm({ ...roomForm, floor: e.target.value })} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRoomOpen(false)}>Cancel</Button>
            <Button onClick={saveRoom} disabled={mutate.isPending || !roomForm.room_number || !roomForm.room_type_id}>Save</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={rateOpen} onOpenChange={setRateOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingRate ? "Edit rate plan" : "Add rate plan"}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Name</Label>
              <Input value={rateForm.name} onChange={(e) => setRateForm({ ...rateForm, name: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Code</Label>
              <Input value={rateForm.code} onChange={(e) => setRateForm({ ...rateForm, code: e.target.value })} />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label>Room type</Label>
              <Select
                value={rateForm.room_type_id || "__all__"}
                onValueChange={(v) => setRateForm({ ...rateForm, room_type_id: v === "__all__" ? "" : v })}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All room types</SelectItem>
                  {types.map((rt) => (
                    <SelectItem key={rt.id} value={rt.id}>{rt.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Start date</Label>
              <Input type="date" value={rateForm.start_date} onChange={(e) => setRateForm({ ...rateForm, start_date: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>End date</Label>
              <Input type="date" value={rateForm.end_date} onChange={(e) => setRateForm({ ...rateForm, end_date: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Nightly rate ({currency})</Label>
              <Input value={rateForm.nightly_rate} onChange={(e) => setRateForm({ ...rateForm, nightly_rate: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Priority</Label>
              <Input value={rateForm.priority} onChange={(e) => setRateForm({ ...rateForm, priority: e.target.value })} />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <Label>Notes</Label>
              <Textarea value={rateForm.notes} onChange={(e) => setRateForm({ ...rateForm, notes: e.target.value })} />
            </div>
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <input
                type="checkbox"
                className="size-4 rounded border"
                checked={rateForm.is_active}
                onChange={(e) => setRateForm({ ...rateForm, is_active: e.target.checked })}
              />
              Active
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRateOpen(false)}>Cancel</Button>
            <Button
              onClick={saveRate}
              disabled={
                mutate.isPending ||
                !rateForm.name ||
                !rateForm.code ||
                !rateForm.start_date ||
                !rateForm.end_date ||
                !rateForm.nightly_rate
              }
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={taxOpen} onOpenChange={setTaxOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingTax ? "Edit tax rule" : "Add tax rule"}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1.5">
              <Label>Name</Label>
              <Input value={taxForm.name} onChange={(e) => setTaxForm({ ...taxForm, name: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>Rate %</Label>
                <Input value={taxForm.rate_percent} onChange={(e) => setTaxForm({ ...taxForm, rate_percent: e.target.value })} />
              </div>
              <div className="space-y-1.5">
                <Label>Applies to</Label>
                <Select value={taxForm.applies_to} onValueChange={(v) => setTaxForm({ ...taxForm, applies_to: v })}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="room">Room</SelectItem>
                    <SelectItem value="fnb">F&amp;B / minibar</SelectItem>
                    <SelectItem value="all">All charges</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>ERP tax template id</Label>
              <Input
                value={taxForm.tax_template_id}
                onChange={(e) => setTaxForm({ ...taxForm, tax_template_id: e.target.value })}
                placeholder="Optional tax_template.id from erpclaw"
              />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 rounded border"
                checked={taxForm.is_default}
                onChange={(e) => setTaxForm({ ...taxForm, is_default: e.target.checked })}
              />
              Default rule
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 rounded border"
                checked={taxForm.is_active}
                onChange={(e) => setTaxForm({ ...taxForm, is_active: e.target.checked })}
              />
              Active
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTaxOpen(false)}>Cancel</Button>
            <Button onClick={saveTax} disabled={mutate.isPending || !taxForm.name || !taxForm.rate_percent}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
