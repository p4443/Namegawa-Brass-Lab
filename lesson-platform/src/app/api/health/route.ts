import { NextResponse } from "next/server";

import { serverConfigReady } from "@/lib/env";

export function GET() {
  return NextResponse.json({
    ok: true,
    configured: {
      core: serverConfigReady(),
      line: Boolean(process.env.LINE_CHANNEL_ID && process.env.NEXT_PUBLIC_LINE_LIFF_ID),
      officialBookingApi: true,
    },
  });
}
