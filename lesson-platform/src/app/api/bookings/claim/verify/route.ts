import { createHmac } from "node:crypto";

import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { getServerEnv, serverConfigReady } from "@/lib/env";
import { sessionCookieName, verifyPortalSession } from "@/lib/session";
import { createAdminClient } from "@/lib/supabase";

function digest(reservationId: string, lineUserId: string, code: string) {
  return createHmac("sha256", getServerEnv("PORTAL_SESSION_SECRET"))
    .update(`${reservationId}:${lineUserId}:${code}`)
    .digest("hex");
}

export async function POST(request: Request) {
  if (!serverConfigReady()) {
    return NextResponse.json({ error: "現在、予約連携を利用できません。" }, { status: 503 });
  }
  const session = await verifyPortalSession((await cookies()).get(sessionCookieName)?.value);
  if (!session) return NextResponse.json({ error: "LINEでログインしてください。" }, { status: 401 });

  const body = (await request.json().catch(() => null)) as { reservation_id?: unknown; code?: unknown } | null;
  const reservationId = typeof body?.reservation_id === "string" ? body.reservation_id.trim().toUpperCase() : "";
  const code = typeof body?.code === "string" ? body.code.trim() : "";
  if (!/^R-\d{8}-\d{3,}$/.test(reservationId) || !/^\d{6}$/.test(code)) {
    return NextResponse.json({ error: "受付番号と6桁の確認コードを入力してください。" }, { status: 400 });
  }

  const { data, error } = await createAdminClient().rpc("claim_official_booking", {
    p_official_reservation_id: reservationId,
    p_guardian_line_user_id: session.lineUserId,
    p_code_digest: digest(reservationId, session.lineUserId, code),
  });
  if (error) return NextResponse.json({ error: "予約を紐付けられませんでした。" }, { status: 502 });
  if (data !== "claimed") {
    return NextResponse.json({ error: data === "already_linked" ? "この予約は別のLINEアカウントに紐付いています。" : "確認コードが違うか、有効期限が切れています。" }, { status: 409 });
  }
  return NextResponse.json({ linked: true });
}