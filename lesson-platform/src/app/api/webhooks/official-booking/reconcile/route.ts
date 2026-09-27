import { createHash, timingSafeEqual } from "node:crypto";

import { NextResponse } from "next/server";

import { pushLineTextMessage } from "@/lib/line-messaging";
import { createAdminClient } from "@/lib/supabase";

export const maxDuration = 30;

const lessonTypes = new Set(["体験レッスン", "小学生", "中学生", "高校生以上・大人"]);

type SourceBooking = {
  reservation_id?: unknown;
  status?: unknown;
  lesson_type?: unknown;
  preferred_date?: unknown;
  preferred_time?: unknown;
  duration_minutes?: unknown;
};

type ReconciledBooking = {
  status: "予約済み" | "確定";
  lessonType: string;
  startsAt: string;
  endsAt: string;
};

function authorized(request: Request) {
  const expected = process.env.OFFICIAL_BOOKING_WEBHOOK_SECRET;
  const actual = request.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (!expected || !actual) {
    console.warn("Official booking reconciliation authorization failed", {
      expectedLength: expected?.length ?? 0,
      actualLength: actual?.length ?? 0,
    });
    return false;
  }

  const expectedBuffer = Buffer.from(expected);
  const actualBuffer = Buffer.from(actual);
  const matches = expectedBuffer.length === actualBuffer.length && timingSafeEqual(expectedBuffer, actualBuffer);
  if (!matches) {
    console.warn("Official booking reconciliation authorization failed", {
      expectedLength: expected.length,
      actualLength: actual.length,
      expectedFingerprint: createHash("sha256").update(expected).digest("hex").slice(0, 12),
      actualFingerprint: createHash("sha256").update(actual).digest("hex").slice(0, 12),
    });
  }
  return matches;
}

function formatLessonDate(value: string) {
  return new Intl.DateTimeFormat("ja-JP", {
    dateStyle: "long",
    timeStyle: "short",
    timeZone: "Asia/Tokyo",
  }).format(new Date(value));
}

function parseBooking(source: SourceBooking): [string, ReconciledBooking | null] | null {
  const reservationId = typeof source.reservation_id === "string" ? source.reservation_id.trim() : "";
  if (!/^R-\d{8}-\d{3,}$/.test(reservationId)) return null;

  const lessonType = typeof source.lesson_type === "string" ? source.lesson_type.trim() : "";
  const preferredDate = typeof source.preferred_date === "string" ? source.preferred_date.trim() : "";
  const preferredTime = typeof source.preferred_time === "string" ? source.preferred_time.trim() : "";
  const durationMinutes = Number(source.duration_minutes);
  const status = source.status === "確定"
    ? "確定"
    : source.status === "確認中" || source.status === "調整中" || source.status === "予約済み"
      ? "予約済み"
      : null;
  const startsAt = new Date(`${preferredDate}T${preferredTime}:00+09:00`);
  if (!status || !lessonTypes.has(lessonType)
    || !/^\d{4}-\d{2}-\d{2}$/.test(preferredDate) || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(preferredTime)
    || Number.isNaN(startsAt.getTime()) || !Number.isInteger(durationMinutes)
    || durationMinutes <= 0 || durationMinutes > 480) {
    return [reservationId, null];
  }
  return [reservationId, {
    status,
    lessonType,
    startsAt: startsAt.toISOString(),
    endsAt: new Date(startsAt.getTime() + durationMinutes * 60_000).toISOString(),
  }];
}

export async function POST(request: Request) {
  if (!authorized(request)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const body = (await request.json().catch(() => null)) as { bookings?: unknown } | null;
  if (!Array.isArray(body?.bookings) || body.bookings.length > 1000) {
    return NextResponse.json({ error: "Invalid bookings" }, { status: 400 });
  }
  const parsed = body.bookings.map((booking) => parseBooking(booking as SourceBooking));
  if (parsed.some((booking) => booking === null)) {
    return NextResponse.json({ error: "Invalid booking details" }, { status: 400 });
  }
  const officialBookings = new Map(parsed as [string, ReconciledBooking | null][]);
  const supabase = createAdminClient();
  const { data, error } = await supabase
    .from("lesson_bookings")
    .select("official_reservation_id, guardian_line_user_id, lesson_type, starts_at, status, line_notified_status")
    .gte("starts_at", new Date().toISOString());
  if (error) return NextResponse.json({ error: "Booking lookup failed" }, { status: 502 });

  let updated = 0;
  let cancelled = 0;
  let notified = 0;
  for (const row of data ?? []) {
    const reservationId = typeof row.official_reservation_id === "string" ? row.official_reservation_id : "";
    if (!reservationId) continue;
    const active = officialBookings.has(reservationId);
    const official = officialBookings.get(reservationId);
    if (active && !official) continue;
    const values = official
      ? {
          status: official.status,
          lesson_type: official.lessonType,
          starts_at: official.startsAt,
          ends_at: official.endsAt,
          updated_at: new Date().toISOString(),
        }
      : { status: "キャンセル", updated_at: new Date().toISOString() };
    const { error: updateError } = await supabase
      .from("lesson_bookings")
      .update(values)
      .eq("official_reservation_id", reservationId);
    if (updateError) return NextResponse.json({ error: "Booking reconciliation failed" }, { status: 502 });
    if (official) updated += 1;
    else {
      cancelled += 1;
      if (row.guardian_line_user_id && row.line_notified_status !== "キャンセル") {
        const sent = await pushLineTextMessage(
          row.guardian_line_user_id,
          [
            "レッスン予約がキャンセルされました。",
            `受付番号: ${reservationId}`,
            `日時: ${formatLessonDate(row.starts_at)}`,
            `内容: ${row.lesson_type}`,
            "予定確認にも反映しました。",
          ].join("\n"),
        );
        if (!sent) return NextResponse.json({ error: "LINE notification failed" }, { status: 502 });
        const { error: notifiedError } = await supabase
          .from("lesson_bookings")
          .update({ line_notified_status: "キャンセル", updated_at: new Date().toISOString() })
          .eq("official_reservation_id", reservationId)
          .eq("status", "キャンセル");
        if (notifiedError) return NextResponse.json({ error: "Notification status update failed" }, { status: 502 });
        notified += 1;
      }
    }
  }

  return NextResponse.json({ ok: true, updated, cancelled, notified });
}
