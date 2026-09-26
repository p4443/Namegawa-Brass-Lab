import { timingSafeEqual } from "node:crypto";

import { NextResponse } from "next/server";

import { pushLineTextMessage } from "@/lib/line-messaging";
import { createAdminClient } from "@/lib/supabase";

export const maxDuration = 30;
const lessonTypes = new Set(["体験レッスン", "小学生", "中学生", "高校生以上・大人"]);

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
    booking?: {
      guardian_line_user_id?: unknown;
      lesson_type?: unknown;
      preferred_date?: unknown;
      preferred_time?: unknown;
      duration_minutes?: unknown;
      duplicate?: unknown;
    };
  } | null;
  const reservationId = typeof body?.reservation_id === "string" ? body.reservation_id.trim() : "";
  const deleted = body?.status === "削除";
  const status = body?.status === "確定" || body?.status === "キャンセル"
    ? body.status
    : body?.status === "確認中" ? "予約済み" : "";
  if (!/^R-\d{8}-\d{3,}$/.test(reservationId) || (!status && !deleted)) {
    return NextResponse.json({ error: "Invalid booking status" }, { status: 400 });
  }

  const supabase = createAdminClient();
  const { data: booking, error } = await supabase
    .from("lesson_bookings")
    .select("guardian_line_user_id, lesson_type, starts_at, status, line_notified_status")
    .eq("official_reservation_id", reservationId)
    .maybeSingle();
  if (error) return NextResponse.json({ error: "Booking lookup failed" }, { status: 502 });
  if (deleted) {
    if (!booking) return NextResponse.json({ ok: true, duplicate: true });
    const { error: deleteError } = await supabase
      .from("lesson_bookings")
      .delete()
      .eq("official_reservation_id", reservationId);
    if (deleteError) return NextResponse.json({ error: "Booking deletion failed" }, { status: 502 });
    return NextResponse.json({ ok: true, deleted: true });
  }

  const source = body?.booking;
  if (!booking && source) {
    const lessonType = typeof source.lesson_type === "string" ? source.lesson_type.trim() : "";
    const preferredDate = typeof source.preferred_date === "string" ? source.preferred_date.trim() : "";
    const preferredTime = typeof source.preferred_time === "string" ? source.preferred_time.trim() : "";
    const durationMinutes = Number(source.duration_minutes);
    const guardianLineUserId = source.duplicate !== true && typeof source.guardian_line_user_id === "string"
      && source.guardian_line_user_id.trim() && source.guardian_line_user_id.trim().length <= 255
      ? source.guardian_line_user_id.trim()
      : null;
    const startsAt = new Date(`${preferredDate}T${preferredTime}:00+09:00`);
    if (!lessonTypes.has(lessonType) || !/^\d{4}-\d{2}-\d{2}$/.test(preferredDate)
      || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(preferredTime) || Number.isNaN(startsAt.getTime())
      || !Number.isInteger(durationMinutes) || durationMinutes <= 0 || durationMinutes > 480) {
      return NextResponse.json({ error: "Invalid booking details" }, { status: 400 });
    }
    const endsAt = new Date(startsAt.getTime() + durationMinutes * 60_000);
    const { error: insertError } = await supabase.from("lesson_bookings").insert({
      official_reservation_id: reservationId,
      cal_booking_id: null,
      guardian_line_user_id: guardianLineUserId,
      lesson_type: lessonType,
      starts_at: startsAt.toISOString(),
      ends_at: endsAt.toISOString(),
      status,
      updated_at: new Date().toISOString(),
    });
    if (insertError) return NextResponse.json({ error: "Booking creation failed" }, { status: 502 });
    return NextResponse.json({ ok: true, created: true });
  }
  if (!booking) return NextResponse.json({ error: "Booking not found" }, { status: 404 });
  if (!source && booking.status === status && booking.line_notified_status === status) {
    return NextResponse.json({ ok: true, duplicate: true });
  }

  const updates: {
    status: string;
    updated_at: string;
    lesson_type?: string;
    starts_at?: string;
    ends_at?: string;
  } = { status, updated_at: new Date().toISOString() };
  let notificationLessonType = booking.lesson_type;
  let notificationStartsAt = booking.starts_at;
  if (source) {
    const lessonType = typeof source.lesson_type === "string" ? source.lesson_type.trim() : "";
    const preferredDate = typeof source.preferred_date === "string" ? source.preferred_date.trim() : "";
    const preferredTime = typeof source.preferred_time === "string" ? source.preferred_time.trim() : "";
    const durationMinutes = Number(source.duration_minutes);
    const startsAt = new Date(`${preferredDate}T${preferredTime}:00+09:00`);
    if (!lessonTypes.has(lessonType) || !/^\d{4}-\d{2}-\d{2}$/.test(preferredDate)
      || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(preferredTime) || Number.isNaN(startsAt.getTime())
      || !Number.isInteger(durationMinutes) || durationMinutes <= 0 || durationMinutes > 480) {
      return NextResponse.json({ error: "Invalid booking details" }, { status: 400 });
    }
    updates.lesson_type = lessonType;
    updates.starts_at = startsAt.toISOString();
    updates.ends_at = new Date(startsAt.getTime() + durationMinutes * 60_000).toISOString();
    notificationLessonType = lessonType;
    notificationStartsAt = updates.starts_at;
  }

  const { error: updateError } = await supabase
    .from("lesson_bookings")
    .update(updates)
    .eq("official_reservation_id", reservationId);
  if (updateError) return NextResponse.json({ error: "Booking update failed" }, { status: 502 });

  if ((status !== "確定" && status !== "キャンセル") || !booking.guardian_line_user_id
    || booking.line_notified_status === status) {
    return NextResponse.json({ ok: true });
  }

  const message = status === "キャンセル"
    ? [
        "レッスン予約がキャンセルされました。",
        `受付番号: ${reservationId}`,
        `日時: ${formatLessonDate(notificationStartsAt)}`,
        `内容: ${notificationLessonType}`,
        "予定確認にも反映しました。",
      ]
    : [
        "レッスン予約が確定しました。",
        `受付番号: ${reservationId}`,
        `日時: ${formatLessonDate(notificationStartsAt)}`,
        `内容: ${notificationLessonType}`,
        "詳細はトーク画面下部メニューの「予定確認」から確認できます。",
      ];
  const sent = await pushLineTextMessage(booking.guardian_line_user_id, message.join("\n"));
  if (!sent) return NextResponse.json({ error: "LINE notification failed" }, { status: 502 });

  const { error: notifiedError } = await supabase
    .from("lesson_bookings")
    .update({ line_notified_status: status, updated_at: new Date().toISOString() })
    .eq("official_reservation_id", reservationId)
    .eq("status", status);
  if (notifiedError) return NextResponse.json({ error: "Notification status update failed" }, { status: 502 });

  return NextResponse.json({ ok: true });
}
