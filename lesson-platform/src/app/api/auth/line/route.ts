import { NextResponse } from "next/server";

import { serverConfigReady } from "@/lib/env";
import { verifyLineIdToken } from "@/lib/line";
import { createPortalSession, sessionCookieName } from "@/lib/session";
import { createAdminClient } from "@/lib/supabase";

export async function POST(request: Request) {
  if (!serverConfigReady() || !process.env.LINE_CHANNEL_ID) {
    return NextResponse.json({ error: "LINEログインは準備中です。" }, { status: 503 });
  }

  const body = (await request.json().catch(() => null)) as { idToken?: unknown } | null;
  if (typeof body?.idToken !== "string" || body.idToken.length > 4096) {
    return NextResponse.json({ error: "認証情報が正しくありません。" }, { status: 400 });
  }

  const profile = await verifyLineIdToken(body.idToken);
  if (!profile) return NextResponse.json({ error: "LINE認証を確認できませんでした。" }, { status: 401 });

  const supabase = createAdminClient();
  const { error } = await supabase.from("guardians").upsert(
    {
      line_user_id: profile.lineUserId,
      display_name: profile.displayName,
      last_login_at: new Date().toISOString(),
    },
    { onConflict: "line_user_id" },
  );
  if (error) return NextResponse.json({ error: "ログイン情報を保存できませんでした。" }, { status: 502 });

  const response = NextResponse.json({ ok: true });
  response.cookies.set(sessionCookieName, await createPortalSession(profile), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 7,
  });
  return response;
}
