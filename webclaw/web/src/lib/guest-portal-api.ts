/** Public (unauthenticated) guest portal API client. */

const API_BASE = "/api/v1";

export type GuestStayPayload = {
  confirmation_code: string;
  status: string;
  pre_checked_in: boolean;
  checked_in: boolean;
  checkout_requested: boolean;
  registration_signed: boolean;
  hotel: {
    name?: string;
    address?: string;
    phone?: string;
    email?: string;
    currency: string;
    check_in_time: string;
    check_out_time: string;
  };
  guest: {
    first_name?: string;
    last_name?: string;
    email?: string;
    phone?: string;
    nationality?: string;
    id_number?: string;
  };
  stay: {
    check_in: string;
    check_out: string;
    room_type?: string;
    room_number?: string | null;
    rate: string;
    adults?: number;
    children?: number;
  };
  folio: {
    id?: string | null;
    balance: string;
    status?: string | null;
    charges: Array<{
      id: string;
      description: string;
      amount: string;
      charge_type: string;
      posting_date: string;
    }>;
  };
  documents: Array<{
    id: string;
    doc_type: string;
    file_name?: string;
    mime_type?: string;
    created_at?: string;
  }>;
  can_pre_register: boolean;
  can_pay: boolean;
  can_request_checkout: boolean;
};

async function guestFetch(token: string, path: string, init?: RequestInit) {
  const res = await fetch(`${API_BASE}/guest/${encodeURIComponent(token)}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status === "error") {
    throw new Error(data.message || `Request failed (${res.status})`);
  }
  return data;
}

export async function fetchGuestStay(token: string): Promise<GuestStayPayload> {
  const data = await guestFetch(token, "");
  return data.stay as GuestStayPayload;
}

export async function submitPreRegister(
  token: string,
  body: {
    first_name?: string;
    last_name?: string;
    email?: string;
    phone?: string;
    nationality?: string;
    id_number?: string;
    signature_data_url?: string;
  },
): Promise<GuestStayPayload> {
  const data = await guestFetch(token, "/pre-register", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return data.stay as GuestStayPayload;
}

export async function uploadIdDocument(
  token: string,
  file: File,
  docType: "id_photo" | "passport" = "id_photo",
) {
  const form = new FormData();
  form.append("file", file);
  form.append("doc_type", docType);
  const res = await fetch(`${API_BASE}/guest/${encodeURIComponent(token)}/id-document`, {
    method: "POST",
    body: form,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.status === "error") {
    throw new Error(data.message || "Upload failed");
  }
  return data;
}

export async function createGuestPaymentIntent(token: string, amount?: string) {
  const data = await guestFetch(token, "/payment-intent", {
    method: "POST",
    body: JSON.stringify(amount ? { amount } : {}),
  });
  return {
    client_secret: String(data.client_secret),
    payment_intent_id: String(data.payment_intent_id),
    publishable_key: String(data.publishable_key),
    amount: String(data.amount || ""),
    currency: String(data.currency || "usd"),
  };
}

export async function confirmGuestPayment(token: string, paymentIntentId: string) {
  return guestFetch(token, "/payment-confirm", {
    method: "POST",
    body: JSON.stringify({ payment_intent_id: paymentIntentId }),
  });
}

export async function requestGuestCheckout(token: string) {
  return guestFetch(token, "/request-checkout", {
    method: "POST",
    body: "{}",
  });
}

export function guestDocumentUrl(token: string, docId: string) {
  return `${API_BASE}/guest/${encodeURIComponent(token)}/documents/${encodeURIComponent(docId)}`;
}
