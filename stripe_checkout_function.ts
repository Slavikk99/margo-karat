// ============================================================
// MARGO KARAT — Supabase Edge Function: создание Stripe Checkout
// Деплой:  supabase functions deploy stripe-checkout --no-verify-jwt
// Секреты: supabase secrets set STRIPE_SECRET_KEY=sk_live_...
// После деплоя URL функции вставить в MARGO_CONFIG.STRIPE_CHECKOUT_URL (index.html)
//
// Также создать вторую функцию stripe-webhook (см. низ файла) и вписать её URL
// в Stripe → Developers → Webhooks (событие checkout.session.completed).
// Деньги выводятся на банковский счёт автоматически: Stripe → Settings → Payouts.
// ============================================================
import Stripe from "npm:stripe@14";
import { createClient } from "npm:@supabase/supabase-js@2";

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!);
const sb = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "content-type",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const { order_id, kind, amount, success_url, cancel_url } = await req.json();

    const session = await stripe.checkout.sessions.create({
      mode: "payment",
      payment_method_types: ["card"], // карта включает Apple Pay / Google Pay автоматически
      line_items: [{
        price_data: {
          currency: "eur",
          unit_amount: Math.round(Number(amount) * 100),
          product_data: {
            name: kind === "consult"
              ? "MARGO KARAT — Приватная консультация"
              : "MARGO KARAT — Персональный разбор",
          },
        },
        quantity: 1,
      }],
      success_url,
      cancel_url,
      metadata: { order_id, kind },
    });

    const table = kind === "consult" ? "private_consultations" : "service_orders";
    await sb.from(table).update({ stripe_session_id: session.id }).eq("id", order_id);

    return new Response(JSON.stringify({ url: session.url }), {
      headers: { ...CORS, "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }), {
      status: 400,
      headers: { ...CORS, "Content-Type": "application/json" },
    });
  }
});

/* ============================================================
   ВТОРАЯ ФУНКЦИЯ — stripe-webhook (отдельный файл index.ts):
   подтверждение оплаты → payment_status='оплачен' → AI-агент подхватит заказ.

import Stripe from "npm:stripe@14";
import { createClient } from "npm:@supabase/supabase-js@2";
const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!);
const sb = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

Deno.serve(async (req) => {
  const sig = req.headers.get("stripe-signature")!;
  const body = await req.text();
  const event = await stripe.webhooks.constructEventAsync(
    body, sig, Deno.env.get("STRIPE_WEBHOOK_SECRET")!);
  if (event.type === "checkout.session.completed") {
    const s = event.data.object as Stripe.Checkout.Session;
    const table = s.metadata!.kind === "consult" ? "private_consultations" : "service_orders";
    await sb.from(table).update({ payment_status: "оплачен" }).eq("id", s.metadata!.order_id);
  }
  return new Response("ok");
});
============================================================ */
