"use client";

import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { HotelOpsHeader } from "@/components/hotel/hotel-ops-header";
import { FolioCardPayment } from "@/components/hotel/folio-card-payment";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useEntityList, useEntityMutation } from "@/lib/hooks";
import { useToast } from "@/components/toast-provider";
import {
  SKILL,
  type Folio,
  type FolioCharge,
  type Hotel,
  type LedgerEntry,
  formatMoney,
  guestName,
  hotelCurrency,
  itemsFrom,
  parseHotelSettings,
} from "@/lib/hospitality";
import { CreditCard, Landmark, Loader2, Receipt } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getHotelPaymentsStatus } from "@/lib/billing-api";

const CHARGE_LABEL: Record<string, string> = {
  room: "Room",
  tax: "Tax",
  fnb: "Restaurant / minibar",
  other: "Other",
  payment: "Payments",
};

export default function HotelBillingPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const mutate = useEntityMutation(SKILL);

  const foliosQ = useEntityList(
    SKILL,
    "list-folios",
    { limit: "200" },
    { enabled: perms.canSeeFolios },
  );
  const hotelsQ = useEntityList(
    SKILL,
    "list-hotels",
    { limit: "20" },
    { enabled: perms.canSeeFolios },
  );
  const folios = itemsFrom<Folio>(foliosQ.data, "folios");
  const hotels = itemsFrom<Hotel>(hotelsQ.data, "hotels");
  const currency = hotelCurrency(hotels[0]?.currency);
  const hotelSettings = parseHotelSettings(hotels[0]?.settings_json);
  const hotelPaymentsQ = useQuery({
    queryKey: ["hotel-payments-status"],
    queryFn: getHotelPaymentsStatus,
    enabled: perms.canWriteFolios,
    staleTime: 60_000,
  });
  const cardEnabled =
    Boolean(hotelPaymentsQ.data?.configured) ||
    (hotelSettings.payment_gateway === "stripe" &&
      Boolean(hotelSettings.payment_publishable_key));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = folios.find((f) => f.id === selectedId) ?? folios[0] ?? null;
  const folioId = selected?.id ?? "";

  const chargesQ = useEntityList(
    SKILL,
    "list-folio-charges",
    { folio_id: folioId, limit: "200" },
    { enabled: perms.canSeeFolios && !!folioId },
  );
  const ledgerQ = useEntityList(
    SKILL,
    "list-ledger-entries",
    { folio_id: folioId, limit: "200" },
    { enabled: perms.canSeeLedger && !!folioId },
  );
  const charges = itemsFrom<FolioCharge>(chargesQ.data, "charges");
  const ledger = itemsFrom<LedgerEntry>(ledgerQ.data, "entries");

  const breakdown = useMemo(() => {
    const sums: Record<string, number> = {};
    for (const c of charges) {
      const key = c.charge_type || "other";
      sums[key] = (sums[key] || 0) + Number(c.amount || 0);
    }
    return sums;
  }, [charges]);

  const [chargeOpen, setChargeOpen] = useState(false);
  const [settleOpen, setSettleOpen] = useState(false);
  const [cardOpen, setCardOpen] = useState(false);
  const [depositOpen, setDepositOpen] = useState(false);
  const [chargeForm, setChargeForm] = useState({
    description: "",
    amount: "",
    charge_type: "fnb",
  });
  const [settleForm, setSettleForm] = useState({
    payment_method: "cash",
    reference: "",
  });

  async function postCharge() {
    if (!folioId) return;
    try {
      const res = await mutate.mutateAsync({
        action: "add-folio-charge",
        body: { folio_id: folioId, ...chargeForm },
      });
      const taxCount = Array.isArray(res.taxes) ? res.taxes.length : 0;
      showToast({
        type: "success",
        message: taxCount
          ? `Charge posted with ${taxCount} tax line(s)`
          : "Charge posted",
      });
      setChargeOpen(false);
      setChargeForm({ description: "", amount: "", charge_type: "fnb" });
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Post failed" });
    }
  }

  async function postTariff() {
    if (!folioId) return;
    try {
      const res = await mutate.mutateAsync({
        action: "post-room-tariff",
        body: { folio_id: folioId },
      });
      const count = Number(res.posted_count ?? 0);
      const taxes = Number(res.tax_posted_count ?? 0);
      showToast({
        type: count ? "success" : "info",
        message: count
          ? `Room tariff posted${taxes ? ` + ${taxes} tax` : ""}${res.gl_posted_count ? " (GL)" : ""}`
          : "Tonight's room tariff is already posted",
      });
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Post failed" });
    }
  }

  async function postAllTariffs() {
    try {
      const res = await mutate.mutateAsync({ action: "post-room-tariff", body: {} });
      showToast({
        type: "success",
        message: `Posted ${res.posted_count ?? 0} tariff(s)${res.tax_posted_count ? `, ${res.tax_posted_count} tax` : ""}${res.gl_posted_count ? `, ${res.gl_posted_count} GL` : ""}`,
      });
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Night audit failed" });
    }
  }

  async function settleBill() {
    if (!folioId) return;
    try {
      const res = await mutate.mutateAsync({
        action: "settle-folio",
        body: {
          folio_id: folioId,
          payment_method: settleForm.payment_method,
          reference: settleForm.reference || undefined,
        },
      });
      showToast({
        type: "success",
        message: res.gl_posted
          ? `Bill settled — payment posted to erpclaw GL (${formatMoney(res.amount, currency)})`
          : `Bill settled (${formatMoney(res.amount, currency)})`,
      });
      setSettleOpen(false);
    } catch (err) {
      showToast({ type: "error", message: err instanceof Error ? err.message : "Settle failed" });
    }
  }

  if (!perms.canSeeFolios) {
    return (
      <div className="p-6 text-sm text-muted-foreground">
        Financial folios are hidden for your role. Ask a manager or accountant for billing access.
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Guest billing</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <HotelOpsHeader
          title="Guest folios & settlement"
          subtitle="Room charges, incidentals, tax breakdown, and settle-bill posting through erpclaw-engine"
          perms={perms}
          actions={
            perms.canWriteFolios ? (
              <Button size="sm" variant="outline" className="gap-1.5" onClick={postAllTariffs}>
                <Landmark className="h-3.5 w-3.5" />
                Post in-house tariffs
              </Button>
            ) : undefined
          }
        />

        {foliosQ.isLoading ? (
          <div className="flex h-48 items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <div className="grid gap-6 lg:grid-cols-5">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle className="text-base">Folios</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                {folios.length === 0 ? (
                  <p className="p-4 text-sm text-muted-foreground">No folios yet.</p>
                ) : (
                  <ul className="divide-y">
                    {folios.map((f) => (
                      <li key={f.id}>
                        <button
                          type="button"
                          onClick={() => setSelectedId(f.id)}
                          className={`w-full px-4 py-3 text-left hover:bg-accent/50 ${selected?.id === f.id ? "bg-accent" : ""}`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-sm font-medium">{guestName(f)}</span>
                            <Badge
                              variant={
                                f.status === "open"
                                  ? "default"
                                  : f.status === "cancelled"
                                    ? "outline"
                                    : "secondary"
                              }
                            >
                              {f.status}
                            </Badge>
                          </div>
                          <p className="mt-0.5 text-xs text-muted-foreground">
                            Room {f.room_number || "—"} · {formatMoney(f.balance, currency)} due
                          </p>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            <div className="space-y-4 lg:col-span-3">
              {!selected ? (
                <Card>
                  <CardContent className="p-8 text-center text-sm text-muted-foreground">
                    Select a folio to view the guest ledger.
                  </CardContent>
                </Card>
              ) : (
                <>
                  <Card>
                    <CardHeader className="flex flex-row items-start justify-between space-y-0">
                      <div>
                        <CardTitle className="text-base">{guestName(selected)}</CardTitle>
                        <p className="mt-1 text-sm text-muted-foreground">
                          {selected.check_in} → {selected.check_out} · Room {selected.room_number || "—"}
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-xs text-muted-foreground">Balance due</p>
                        <p className="text-xl font-semibold">{formatMoney(selected.balance, currency)}</p>
                      </div>
                    </CardHeader>
                    {perms.canWriteFolios && selected.status === "open" ? (
                      <CardContent className="flex flex-wrap gap-2">
                        <Button size="sm" variant="outline" onClick={() => setChargeOpen(true)}>
                          Add incidental
                        </Button>
                        <Button size="sm" variant="outline" className="gap-1.5" onClick={postTariff}>
                          <Receipt className="h-3.5 w-3.5" />
                          Post room tariff
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => setSettleOpen(true)}
                          disabled={Number(selected.balance) === 0}
                        >
                          Settle bill
                        </Button>
                        {cardEnabled ? (
                          <Button
                            size="sm"
                            variant="secondary"
                            className="gap-1.5"
                            onClick={() => setCardOpen(true)}
                            disabled={Number(selected.balance) === 0}
                          >
                            <CreditCard className="h-3.5 w-3.5" />
                            Pay with Card
                          </Button>
                        ) : null}
                        {cardEnabled ? (
                          <Button
                            size="sm"
                            variant="outline"
                            className="gap-1.5"
                            onClick={() => setDepositOpen(true)}
                          >
                            <CreditCard className="h-3.5 w-3.5" />
                            Capture deposit
                          </Button>
                        ) : null}
                      </CardContent>
                    ) : null}
                  </Card>

                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    {(["room", "fnb", "tax", "payment"] as const).map((key) => (
                      <Card key={key}>
                        <CardContent className="pt-4">
                          <p className="text-xs text-muted-foreground">{CHARGE_LABEL[key]}</p>
                          <p className="mt-1 text-lg font-semibold">
                            {formatMoney(breakdown[key] || 0, currency)}
                          </p>
                        </CardContent>
                      </Card>
                    ))}
                  </div>

                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">Guest ledger</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Date</TableHead>
                            <TableHead>Description</TableHead>
                            <TableHead>Type</TableHead>
                            <TableHead className="text-right">Amount</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {charges.length === 0 ? (
                            <TableRow>
                              <TableCell colSpan={4} className="text-muted-foreground">
                                No charges posted.
                              </TableCell>
                            </TableRow>
                          ) : (
                            charges.map((c) => (
                              <TableRow key={c.id}>
                                <TableCell>{c.posting_date}</TableCell>
                                <TableCell>{c.description}</TableCell>
                                <TableCell>{CHARGE_LABEL[c.charge_type] || c.charge_type}</TableCell>
                                <TableCell className="text-right">
                                  {c.charge_type === "payment" ? "−" : ""}
                                  {formatMoney(c.amount, currency)}
                                </TableCell>
                              </TableRow>
                            ))
                          )}
                        </TableBody>
                      </Table>
                    </CardContent>
                  </Card>

                  {perms.canSeeLedger ? (
                    <Card>
                      <CardHeader>
                        <CardTitle className="text-base">Double-entry guest ledger</CardTitle>
                      </CardHeader>
                      <CardContent>
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Date</TableHead>
                              <TableHead>Account</TableHead>
                              <TableHead>Description</TableHead>
                              <TableHead className="text-right">Debit</TableHead>
                              <TableHead className="text-right">Credit</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {ledger.length === 0 ? (
                              <TableRow>
                                <TableCell colSpan={5} className="text-muted-foreground">
                                  No ledger entries.
                                </TableCell>
                              </TableRow>
                            ) : (
                              ledger.map((e) => (
                                <TableRow key={e.id}>
                                  <TableCell>{e.posting_date}</TableCell>
                                  <TableCell>{e.account_code || "—"}</TableCell>
                                  <TableCell>{e.description}</TableCell>
                                  <TableCell className="text-right">{formatMoney(e.debit, currency)}</TableCell>
                                  <TableCell className="text-right">{formatMoney(e.credit, currency)}</TableCell>
                                </TableRow>
                              ))
                            )}
                          </TableBody>
                        </Table>
                      </CardContent>
                    </Card>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      Ledger detail is hidden for Front Desk roles.
                    </p>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>

      <Dialog open={chargeOpen} onOpenChange={setChargeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add incidental</DialogTitle>
            <DialogDescription>
              Restaurant, minibar, and other charges. Matching tax rules post automatically.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1.5">
              <Label>Description</Label>
              <Input
                value={chargeForm.description}
                onChange={(e) => setChargeForm({ ...chargeForm, description: e.target.value })}
                placeholder="Minibar"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>Amount</Label>
                <Input
                  value={chargeForm.amount}
                  onChange={(e) => setChargeForm({ ...chargeForm, amount: e.target.value })}
                  placeholder="12.50"
                />
              </div>
              <div className="space-y-1.5">
                <Label>Type</Label>
                <Select
                  value={chargeForm.charge_type}
                  onValueChange={(v) => setChargeForm({ ...chargeForm, charge_type: v })}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="fnb">F&amp;B / minibar</SelectItem>
                    <SelectItem value="room">Room</SelectItem>
                    <SelectItem value="tax">Tax</SelectItem>
                    <SelectItem value="other">Other</SelectItem>
                    <SelectItem value="payment">Payment</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setChargeOpen(false)}>Cancel</Button>
            <Button
              onClick={postCharge}
              disabled={!chargeForm.description || !chargeForm.amount || mutate.isPending}
            >
              Post
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={settleOpen} onOpenChange={setSettleOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Settle bill</DialogTitle>
            <DialogDescription>
              Posts a payment for {formatMoney(selected?.balance, currency)} and double-entry GL
              (Debit Cash / Credit AR) when erpclaw company defaults are set.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1.5">
              <Label>Payment method</Label>
              <Select
                value={settleForm.payment_method}
                onValueChange={(v) => setSettleForm({ ...settleForm, payment_method: v })}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="cash">Cash</SelectItem>
                  <SelectItem value="card">Card</SelectItem>
                  <SelectItem value="bank">Bank</SelectItem>
                  <SelectItem value="other">Other</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Reference</Label>
              <Input
                value={settleForm.reference}
                onChange={(e) => setSettleForm({ ...settleForm, reference: e.target.value })}
                placeholder="Auth code / receipt #"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSettleOpen(false)}>Cancel</Button>
            <Button onClick={settleBill} disabled={mutate.isPending}>
              {mutate.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Settle & post GL"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={cardOpen} onOpenChange={setCardOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Pay with Card</DialogTitle>
            <DialogDescription>
              Charge {formatMoney(selected?.balance, currency)} on the hotel&apos;s Stripe account,
              then post the receipt to erpclaw-engine (Debit Cash / Credit AR).
            </DialogDescription>
          </DialogHeader>
          {cardOpen && folioId && selected ? (
            <FolioCardPayment
              folioId={folioId}
              balance={selected.balance}
              currency={currency}
              onCancel={() => setCardOpen(false)}
              onSuccess={(message) => {
                showToast({ type: "success", message });
                setCardOpen(false);
                void foliosQ.refetch();
                void chargesQ.refetch();
                void ledgerQ.refetch();
              }}
            />
          ) : null}
        </DialogContent>
      </Dialog>

      <Dialog open={depositOpen} onOpenChange={setDepositOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Capture deposit / guarantee</DialogTitle>
            <DialogDescription>
              Charge a booking deposit on the hotel&apos;s Stripe account and post it as a folio
              payment credit (works even when the balance is still zero).
            </DialogDescription>
          </DialogHeader>
          {depositOpen && folioId && selected ? (
            <FolioCardPayment
              folioId={folioId}
              balance={selected.balance}
              currency={currency}
              mode="deposit"
              onCancel={() => setDepositOpen(false)}
              onSuccess={(message) => {
                showToast({ type: "success", message });
                setDepositOpen(false);
                void foliosQ.refetch();
                void chargesQ.refetch();
                void ledgerQ.refetch();
              }}
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
