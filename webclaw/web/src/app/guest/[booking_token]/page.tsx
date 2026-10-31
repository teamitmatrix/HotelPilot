"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { loadStripe, type Stripe } from "@stripe/stripe-js";
import {
  Elements,
  PaymentElement,
  useElements,
  useStripe,
} from "@stripe/react-stripe-js";
import {
  confirmGuestPayment,
  createGuestPaymentIntent,
  fetchGuestStay,
  guestDocumentUrl,
  requestGuestCheckout,
  submitPreRegister,
  uploadIdDocument,
  type GuestStayPayload,
} from "@/lib/guest-portal-api";
import { Loader2 } from "lucide-react";

const stripeCache = new Map<string, Promise<Stripe | null>>();

function stripeFor(key: string) {
  if (!stripeCache.has(key)) stripeCache.set(key, loadStripe(key));
  return stripeCache.get(key)!;
}

function money(amount: string | number, currency = "USD") {
  const n = Number(amount);
  if (Number.isNaN(n)) return String(amount);
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: currency.toUpperCase(),
    }).format(n);
  } catch {
    return `${n} ${currency}`;
  }
}

function SignaturePad({
  onChange,
}: {
  onChange: (dataUrl: string | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);

  const clear = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    onChange(null);
  }, [onChange]);

  useEffect(() => {
    clear();
  }, [clear]);

  function pos(e: React.MouseEvent | React.TouchEvent) {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    if ("touches" in e) {
      const t = e.touches[0];
      return {
        x: (t.clientX - rect.left) * scaleX,
        y: (t.clientY - rect.top) * scaleY,
      };
    }
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY,
    };
  }

  function start(e: React.MouseEvent | React.TouchEvent) {
    e.preventDefault();
    drawing.current = true;
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    const p = pos(e);
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
  }

  function move(e: React.MouseEvent | React.TouchEvent) {
    if (!drawing.current) return;
    e.preventDefault();
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    const p = pos(e);
    ctx.strokeStyle = "#111";
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
  }

  function end() {
    if (!drawing.current) return;
    drawing.current = false;
    const canvas = canvasRef.current;
    if (canvas) onChange(canvas.toDataURL("image/png"));
  }

  return (
    <div className="space-y-2">
      <canvas
        ref={canvasRef}
        width={600}
        height={180}
        className="w-full touch-none rounded-lg border border-stone-300 bg-white"
        onMouseDown={start}
        onMouseMove={move}
        onMouseUp={end}
        onMouseLeave={end}
        onTouchStart={start}
        onTouchMove={move}
        onTouchEnd={end}
      />
      <button
        type="button"
        onClick={clear}
        className="text-sm text-stone-600 underline"
      >
        Clear signature
      </button>
    </div>
  );
}

function PayForm({
  token,
  paymentIntentId,
  amountLabel,
  onDone,
  onCancel,
}: {
  token: string;
  paymentIntentId: string;
  amountLabel: string;
  onDone: (msg: string) => void;
  onCancel: () => void;
}) {
  const stripe = useStripe();
  const elements = useElements();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!stripe || !elements) return;
    setBusy(true);
    setError("");
    try {
      const result = await stripe.confirmPayment({
        elements,
        redirect: "if_required",
      });
      if (result.error) {
        setError(result.error.message || "Payment failed");
        setBusy(false);
        return;
      }
      const pi = result.paymentIntent;
      if (pi?.status !== "succeeded") {
        setError(`Payment status: ${pi?.status || "unknown"}`);
        setBusy(false);
        return;
      }
      const conf = await confirmGuestPayment(token, paymentIntentId || pi.id);
      onDone(String(conf.message || `Paid ${amountLabel}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Payment failed");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <PaymentElement />
      {error ? <p className="text-sm text-red-600">{error}</p> : null}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="flex-1 rounded-lg border border-stone-300 px-4 py-2.5 text-sm font-medium"
          disabled={busy}
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={!stripe || busy}
          className="flex-1 rounded-lg bg-stone-900 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          {busy ? "Processing…" : `Pay ${amountLabel}`}
        </button>
      </div>
    </form>
  );
}

function GuestPayPanel({
  token,
  balance,
  currency,
  onSuccess,
}: {
  token: string;
  balance: string;
  currency: string;
  onSuccess: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [publishableKey, setPublishableKey] = useState("");
  const [paymentIntentId, setPaymentIntentId] = useState("");
  const [open, setOpen] = useState(false);

  async function start() {
    setOpen(true);
    setLoading(true);
    setError("");
    try {
      const intent = await createGuestPaymentIntent(token);
      setClientSecret(intent.client_secret);
      setPublishableKey(intent.publishable_key);
      setPaymentIntentId(intent.payment_intent_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start payment");
    } finally {
      setLoading(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={start}
        className="w-full rounded-xl bg-emerald-700 px-4 py-3 text-sm font-semibold text-white"
      >
        Pay with card · {money(balance, currency)}
      </button>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-8 text-sm text-stone-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Preparing secure payment…
      </div>
    );
  }

  if (error || !clientSecret || !publishableKey) {
    return (
      <div className="space-y-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
        <p>{error || "Card payments are not configured for this hotel."}</p>
        <button type="button" className="underline" onClick={() => setOpen(false)}>
          Close
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-stone-200 bg-white p-4">
      <Elements
        stripe={stripeFor(publishableKey)}
        options={{ clientSecret, appearance: { theme: "stripe" } }}
      >
        <PayForm
          token={token}
          paymentIntentId={paymentIntentId}
          amountLabel={money(balance, currency)}
          onCancel={() => setOpen(false)}
          onDone={() => {
            setOpen(false);
            onSuccess();
          }}
        />
      </Elements>
    </div>
  );
}

export default function GuestPortalPage() {
  const params = useParams();
  const token = String(params?.booking_token || params?.token || "");
  const [stay, setStay] = useState<GuestStayPayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [form, setForm] = useState({
    first_name: "",
    last_name: "",
    email: "",
    phone: "",
    nationality: "",
    id_number: "",
  });
  const [signature, setSignature] = useState<string | null>(null);
  const [idFile, setIdFile] = useState<File | null>(null);

  const reload = useCallback(async () => {
    const data = await fetchGuestStay(token);
    setStay(data);
    setForm({
      first_name: data.guest.first_name || "",
      last_name: data.guest.last_name || "",
      email: data.guest.email || "",
      phone: data.guest.phone || "",
      nationality: data.guest.nationality || "",
      id_number: data.guest.id_number || "",
    });
  }, [token]);

  useEffect(() => {
    if (!token) {
      setError("Missing booking link");
      setLoading(false);
      return;
    }
    (async () => {
      try {
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not load stay");
      } finally {
        setLoading(false);
      }
    })();
  }, [token, reload]);

  async function onPreRegister(e: FormEvent) {
    e.preventDefault();
    if (!signature) {
      setToast("Please sign the registration card");
      return;
    }
    setBusy(true);
    setToast("");
    try {
      if (idFile) {
        await uploadIdDocument(token, idFile, "id_photo");
      }
      const updated = await submitPreRegister(token, {
        ...form,
        signature_data_url: signature,
      });
      setStay(updated);
      setToast("Pre-registration submitted — front desk has been notified.");
    } catch (err) {
      setToast(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCheckoutRequest() {
    setBusy(true);
    setToast("");
    try {
      const res = await requestGuestCheckout(token);
      setToast(String(res.message || "Checkout requested"));
      await reload();
    } catch (err) {
      setToast(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-stone-100">
        <Loader2 className="h-8 w-8 animate-spin text-stone-500" />
      </div>
    );
  }

  if (error || !stay) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-stone-100 p-6">
        <div className="max-w-md rounded-2xl bg-white p-6 text-center shadow-sm">
          <h1 className="text-xl font-semibold text-stone-900">Link unavailable</h1>
          <p className="mt-2 text-sm text-stone-600">{error || "Stay not found"}</p>
        </div>
      </div>
    );
  }

  const currency = stay.hotel.currency || "USD";
  const statusLabel =
    stay.status === "pre_checked_in"
      ? "Pre-checked in"
      : stay.status.replace(/_/g, " ");

  return (
    <div className="min-h-screen bg-gradient-to-b from-stone-100 to-stone-200 text-stone-900">
      <header className="border-b border-stone-200/80 bg-white/90 px-4 py-5 backdrop-blur">
        <div className="mx-auto max-w-lg">
          <p className="text-xs font-medium uppercase tracking-wider text-stone-500">
            Guest portal
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            {stay.hotel.name || "Your stay"}
          </h1>
          <p className="mt-1 text-sm text-stone-600">
            Confirmation {stay.confirmation_code} ·{" "}
            <span className="capitalize">{statusLabel}</span>
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-lg space-y-4 px-4 py-6 pb-24">
        {toast ? (
          <div className="rounded-xl border border-stone-300 bg-white px-4 py-3 text-sm shadow-sm">
            {toast}
          </div>
        ) : null}

        {/* Booking summary */}
        <section className="rounded-2xl bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
            Booking summary
          </h2>
          <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-stone-500">Guest</dt>
              <dd className="font-medium">
                {stay.guest.first_name} {stay.guest.last_name}
              </dd>
            </div>
            <div>
              <dt className="text-stone-500">Room</dt>
              <dd className="font-medium">
                {stay.stay.room_type}
                {stay.stay.room_number ? ` · ${stay.stay.room_number}` : ""}
              </dd>
            </div>
            <div>
              <dt className="text-stone-500">Check-in</dt>
              <dd className="font-medium">
                {stay.stay.check_in}
                <span className="block text-xs text-stone-500">
                  from {stay.hotel.check_in_time}
                </span>
              </dd>
            </div>
            <div>
              <dt className="text-stone-500">Check-out</dt>
              <dd className="font-medium">
                {stay.stay.check_out}
                <span className="block text-xs text-stone-500">
                  by {stay.hotel.check_out_time}
                </span>
              </dd>
            </div>
            <div>
              <dt className="text-stone-500">Nightly rate</dt>
              <dd className="font-medium">{money(stay.stay.rate, currency)}</dd>
            </div>
            <div>
              <dt className="text-stone-500">Balance due</dt>
              <dd className="text-lg font-semibold">
                {money(stay.folio.balance, currency)}
              </dd>
            </div>
          </dl>
          {stay.hotel.address ? (
            <p className="mt-4 text-xs text-stone-500">{stay.hotel.address}</p>
          ) : null}
        </section>

        {/* Folio charges */}
        <section className="rounded-2xl bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
            Folio & charges
          </h2>
          {stay.folio.charges.length === 0 ? (
            <p className="mt-3 text-sm text-stone-500">No charges posted yet.</p>
          ) : (
            <ul className="mt-3 divide-y divide-stone-100">
              {stay.folio.charges.map((c) => (
                <li key={c.id} className="flex justify-between gap-3 py-2 text-sm">
                  <span>
                    <span className="font-medium">{c.description}</span>
                    <span className="mt-0.5 block text-xs text-stone-500">
                      {c.posting_date} · {c.charge_type}
                    </span>
                  </span>
                  <span className="font-medium tabular-nums">
                    {c.charge_type === "payment" ? "−" : ""}
                    {money(c.amount, currency)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {stay.can_pay ? (
            <div className="mt-4">
              <GuestPayPanel
                token={token}
                balance={stay.folio.balance}
                currency={currency}
                onSuccess={async () => {
                  setToast("Payment received. Thank you!");
                  await reload();
                }}
              />
            </div>
          ) : null}
        </section>

        {/* Pre-arrival registration */}
        {stay.can_pre_register ? (
          <section className="rounded-2xl bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
              Pre-arrival registration
            </h2>
            <p className="mt-1 text-sm text-stone-600">
              Upload your ID and sign the registration card. Front desk will see you as{" "}
              <strong>Pre-checked in</strong>.
            </p>
            {stay.pre_checked_in ? (
              <p className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
                You are pre-checked in
                {stay.registration_signed ? " · registration signed" : ""}.
              </p>
            ) : null}
            <form onSubmit={onPreRegister} className="mt-4 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-xs font-medium text-stone-600">
                  First name
                  <input
                    className="mt-1 w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
                    value={form.first_name}
                    onChange={(e) => setForm({ ...form, first_name: e.target.value })}
                    required
                  />
                </label>
                <label className="block text-xs font-medium text-stone-600">
                  Last name
                  <input
                    className="mt-1 w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
                    value={form.last_name}
                    onChange={(e) => setForm({ ...form, last_name: e.target.value })}
                    required
                  />
                </label>
              </div>
              <label className="block text-xs font-medium text-stone-600">
                Email
                <input
                  type="email"
                  className="mt-1 w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                />
              </label>
              <label className="block text-xs font-medium text-stone-600">
                Phone
                <input
                  className="mt-1 w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
                  value={form.phone}
                  onChange={(e) => setForm({ ...form, phone: e.target.value })}
                />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-xs font-medium text-stone-600">
                  Nationality
                  <input
                    className="mt-1 w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
                    value={form.nationality}
                    onChange={(e) => setForm({ ...form, nationality: e.target.value })}
                  />
                </label>
                <label className="block text-xs font-medium text-stone-600">
                  ID / passport #
                  <input
                    className="mt-1 w-full rounded-lg border border-stone-300 px-3 py-2 text-sm"
                    value={form.id_number}
                    onChange={(e) => setForm({ ...form, id_number: e.target.value })}
                  />
                </label>
              </div>
              <label className="block text-xs font-medium text-stone-600">
                ID / passport photo
                <input
                  type="file"
                  accept="image/*"
                  capture="environment"
                  className="mt-1 block w-full text-sm"
                  onChange={(e) => setIdFile(e.target.files?.[0] || null)}
                />
              </label>
              <div>
                <p className="text-xs font-medium text-stone-600">Registration signature</p>
                <p className="mb-2 text-xs text-stone-500">
                  I agree to the hotel registration terms for this stay.
                </p>
                <SignaturePad onChange={setSignature} />
              </div>
              <button
                type="submit"
                disabled={busy}
                className="w-full rounded-xl bg-stone-900 px-4 py-3 text-sm font-semibold text-white disabled:opacity-50"
              >
                {busy ? "Submitting…" : stay.pre_checked_in ? "Update registration" : "Complete pre-check-in"}
              </button>
            </form>
            {stay.documents.length > 0 ? (
              <div className="mt-4 border-t border-stone-100 pt-3">
                <p className="text-xs font-medium text-stone-500">Uploaded documents</p>
                <ul className="mt-2 space-y-1 text-sm">
                  {stay.documents.map((d) => (
                    <li key={d.id}>
                      <a
                        className="text-emerald-800 underline"
                        href={guestDocumentUrl(token, d.id)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {d.doc_type.replace(/_/g, " ")} · {d.file_name || d.id.slice(0, 8)}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>
        ) : null}

        {/* Checkout request */}
        {stay.can_request_checkout ? (
          <section className="rounded-2xl bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
              Digital checkout
            </h2>
            <p className="mt-1 text-sm text-stone-600">
              Settle any balance above, then request checkout. Front desk will complete your
              departure.
            </p>
            {stay.checkout_requested ? (
              <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
                Checkout already requested — waiting for front desk.
              </p>
            ) : (
              <button
                type="button"
                disabled={busy || Number(stay.folio.balance) > 0}
                onClick={onCheckoutRequest}
                className="mt-4 w-full rounded-xl border border-stone-900 px-4 py-3 text-sm font-semibold disabled:opacity-40"
              >
                {Number(stay.folio.balance) > 0
                  ? "Settle balance to request checkout"
                  : "Request checkout"}
              </button>
            )}
          </section>
        ) : null}
      </main>
    </div>
  );
}
