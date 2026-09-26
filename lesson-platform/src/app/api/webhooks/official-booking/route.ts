import { timingSafeEqual } from "node:crypto";

import { NextResponse } from "next/server";

import { pushLineTextMessage } from "@/lib/line-messaging";
import { createAdminClient } from "@/lib/supabase";

export const maxDuration = 30;

function authorized(request: Request) {
  const expected = process.env.OFFICIAL_BOOKING_WEBHOOK_SECRET;
  const actual = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (!expected || !actual) return false;

  const expectedBuffer = Buffer.from(expected);
  const actualBuffer = Buffer.from(actual);
  return expectedBuffer.length === actualBuffer.length && timingSafeEqual(expectedBuffer, actualBuffer);
}

function formatLessonDate(value: string) {
  return new Intl.DateTimeFormat("ja-JP", {
    dateStyle: "long",
    timeStyle: "short",
    timeZone: "Asia/Tokyo",
  }).format(new Date(value));
}

export async function POST(request: Request) {
  if (!authorized(request)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = (await request.json().catch(() => null)) as {
    reservation_id?: unknown;
    status?: unknown;
  } | null;
  const reservationId = typeof body?.reservation_id === "string" ? body.reservation_id.trim() : "";
  if (!/^R-\d{8}-\d{3,}$/.test(reservationId) || body?.status !== "確定") {
    return NextResponse.json({ error: "Invalid booking status" }, { status: 400 });
  }

  const supabase = createAdminClient();
  const { data: booking, error } = await supabase
    .from("lesson_bookings")
    .select("guardian_line_user_id, lesson_type, starts_at, status")
    .eq("official_reservation_id", reservationId)
    .maybeSingle();
  if (error) return NextResponse.json({ error: "Booking lookup failed" }, { status: 502 });
  if (!booking) return NextResponse.json({ error: "Booking not found" }, { status: 404 });
  if (booking.status === "確定") return NextResponse.json({ ok: true, duplicate: true });

  const { error: updateError } = await supabase
    .from("lesson_bookings")
    .update({ status: "確定", updated_at: new Date().toISOString() })
    .eq("official_reservation_id", reservationId);
  if (updateError) return NextResponse.json({ error: "Booking update failed" }, { status: 502 });

  const sent = await pushLineTextMessage(
    booking.guardian_line_user_id,
    [
      "レッスン予約が確定しました。",
      `受付番号: ${reservationId}`,
      `日時: ${formatLessonDate(booking.starts_at)}`,
      `内容: ${booking.lesson_type}`,
      "詳細はトーク画面下部メニューの「予定確認」から確認できます。",
    ].join("\n"),
  );
  if (!sent) return NextResponse.json({ error: "LINE notification failed" }, { status: 502 });

  return NextResponse.json({ ok: true });
}
