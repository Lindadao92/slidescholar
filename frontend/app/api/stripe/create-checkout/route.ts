import { NextRequest, NextResponse } from "next/server";
import { getStripe } from "../../../lib/stripe";

export async function POST(req: NextRequest) {
  try {
    const { email } = await req.json();

    if (!email || typeof email !== "string") {
      return NextResponse.json({ error: "Email is required" }, { status: 400 });
    }

    const priceId = process.env.STRIPE_PRICE_ID;
    const secretKey = process.env.STRIPE_SECRET_KEY;

    console.log("PRICE ID:", JSON.stringify(priceId));
    console.log("STRIPE KEY prefix:", secretKey?.slice(0, 12));

    if (!priceId) {
      console.error("STRIPE_PRICE_ID is not set");
      return NextResponse.json(
        { error: "Stripe is not configured (missing price ID)" },
        { status: 500 }
      );
    }

    if (!secretKey) {
      console.error("STRIPE_SECRET_KEY is not set");
      return NextResponse.json(
        { error: "Stripe is not configured (missing secret key)" },
        { status: 500 }
      );
    }

    const stripe = getStripe();
    const appUrl =
      process.env.NEXT_PUBLIC_APP_URL || "https://slidescholar.vercel.app";

    const session = await stripe.checkout.sessions.create({
      mode: "subscription",
      customer_email: email,
      line_items: [
        {
          price: priceId,
          quantity: 1,
        },
      ],
      success_url: `${appUrl}/configure?subscribed=true&email=${encodeURIComponent(email)}`,
      cancel_url: `${appUrl}/configure`,
    });

    return NextResponse.json({ url: session.url });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    console.error("Stripe checkout error:", message);
    return NextResponse.json(
      { error: `Checkout failed: ${message}` },
      { status: 500 }
    );
  }
}
