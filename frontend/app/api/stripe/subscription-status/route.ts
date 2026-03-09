import { NextRequest, NextResponse } from "next/server";
import { getStripe } from "../../../lib/stripe";

export async function GET(req: NextRequest) {
  const email = req.nextUrl.searchParams.get("email");

  if (!email) {
    return NextResponse.json({ subscribed: false });
  }

  try {
    const stripe = getStripe();
    const customers = await stripe.customers.list({ email, limit: 1 });

    if (customers.data.length === 0) {
      return NextResponse.json({ subscribed: false });
    }

    const customer = customers.data[0];
    const subscriptions = await stripe.subscriptions.list({
      customer: customer.id,
      status: "active",
      limit: 1,
    });

    return NextResponse.json({
      subscribed: subscriptions.data.length > 0,
    });
  } catch (err) {
    console.error("Subscription check error:", err);
    return NextResponse.json({ subscribed: false });
  }
}
