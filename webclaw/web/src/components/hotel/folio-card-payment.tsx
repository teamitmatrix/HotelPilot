"use client";

import { FormEvent, useEffect, useState } from "react";
import { loadStripe, type Stripe } from "@stripe/stripe-js";
import {
  Elements,
  PaymentElement,
  useElements,
  useStripe,
} from "@stripe/react-stripe-js";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import {
  confirmFolioDeposit,
  confirmFolioPayment,
  createFolioDepositIntent,
  createFolioPaymentIntent,
} from "@/lib/billing-api";
import { formatMoney } from "@/lib/hospitality";

const stripeCache = new Map<string, Promise<Stripe | null>>();

function stripePromiseFor(publishableKey: string) {
  if (!stripeCache.has(publishableKey)) {
    stripeCache.set(publishableKey, loadStripe(publishableKey));
  }
  return stripeCache.get(publishableKey)!;
}

function CardPaymentForm({
  folioId,
  paymentIntentId,
  amountLabel,
  mode,
  onSuccess,
  onCancel,
}: {
  folioId: string;
  paymentIntentId: string;
  amountLabel: string;
  mode: "settle" | "deposit";
  onSuccess: (message: string) => void;
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
        setError(result.error.message || "Card payment failed");
        setBusy(false);
        return;
      }
      const status = result.paymentIntent?.status;
      if (status !== "succeeded") {
        setError(`Payment status: ${status || "unknown"}`);
        setBusy(false);
        return;
      }
      if (mode === "deposit") {
        const posted = await confirmFolioDeposit({
          folio_id: folioId,
          payment_intent_id: paymentIntentId,
        });
        onSuccess(
          posted.message ||
            (posted.gl_posted
              ? `Deposit captured and posted to erpclaw GL (${amountLabel})`
              : `Deposit captured (${amountLabel})`),
        );
      } else {
        const posted = await confirmFolioPayment({
          folio_id: folioId,
          payment_intent_id: paymentIntentId,
        });
        onSuccess(
          posted.message ||
            (posted.gl_posted
              ? `Card charged and posted to erpclaw GL (${amountLabel})`
              : `Card charged (${amountLabel})`),
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Payment failed");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <PaymentElement options={{ layout: "tabs" }} />
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" disabled={!stripe || busy}>
          {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          {mode === "deposit" ? `Capture ${amountLabel}` : `Pay ${amountLabel}`}
        </Button>
      </div>
    </form>
  );
}

export function FolioCardPayment({
  folioId,
  balance,
  currency,
  onSuccess,
  onCancel,
  mode = "settle",
  amount,
}: {
  folioId: string;
  balance: string | number;
  currency: string;
  onSuccess: (message: string) => void;
  onCancel: () => void;
  mode?: "settle" | "deposit";
  /** Override charge amount (defaults to balance for settle, policy quote for deposit). */
  amount?: string | number;
}) {
  const [bootError, setBootError] = useState("");
  const [loading, setLoading] = useState(true);
  const [clientSecret, setClientSecret] = useState("");
  const [paymentIntentId, setPaymentIntentId] = useState("");
  const [publishableKey, setPublishableKey] = useState("");
  const [chargedAmount, setChargedAmount] = useState(String(amount ?? balance));
  const amountLabel = formatMoney(chargedAmount, currency);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const intent =
          mode === "deposit"
            ? await createFolioDepositIntent({
                folio_id: folioId,
                amount: amount != null ? String(amount) : undefined,
                currency,
              })
            : await createFolioPaymentIntent({
                folio_id: folioId,
                amount: String(amount ?? balance),
                currency,
              });
        if (cancelled) return;
        setClientSecret(intent.client_secret);
        setPaymentIntentId(intent.payment_intent_id);
        setPublishableKey(intent.publishable_key);
        setChargedAmount(intent.amount || String(amount ?? balance));
      } catch (err) {
        if (!cancelled) {
          setBootError(err instanceof Error ? err.message : "Could not start card payment");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [folioId, balance, currency, mode, amount]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Preparing secure card payment…
      </div>
    );
  }

  if (bootError || !clientSecret || !publishableKey) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-destructive">
          {bootError || "Stripe is not configured for this hotel."}
        </p>
        <p className="text-xs text-muted-foreground">
          Add Stripe keys under Hotel settings → Payments, then try again.
        </p>
        <div className="flex justify-end">
          <Button variant="outline" onClick={onCancel}>
            Close
          </Button>
        </div>
      </div>
    );
  }

  return (
    <Elements
      stripe={stripePromiseFor(publishableKey)}
      options={{
        clientSecret,
        appearance: { theme: "stripe" },
      }}
    >
      <CardPaymentForm
        folioId={folioId}
        paymentIntentId={paymentIntentId}
        amountLabel={amountLabel}
        mode={mode}
        onSuccess={onSuccess}
        onCancel={onCancel}
      />
    </Elements>
  );
}
