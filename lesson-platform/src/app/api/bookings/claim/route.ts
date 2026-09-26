import { createHmac, randomInt } from "node:crypto";

import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { getServerEnv, serverConfigReady } from "@/lib/env";
import { sessionCookieName, verifyPortalSession } from "@/lib/session";
import { createAdminClient } from "@/lib/supabase";

const officialClaimUrl = "https://namegawa-brass-lab.com/api/lesson-reservations/claim-code";

type OfficialBooking = {
  lesson_type?: unknown;
  preferred_date?: unknown;
  preferred_time?: unknown;
  duration_minutes?: unknown;
  status?: unknown;
};

function digest(reservationId: string, lineUserId: string, code: string) {
  return createHmac("sha256", getServerEnv("PORTAL_SESSION_SECRET"))
    .update(`${reservationId}:${lineUserId}:${code}`)
    .digest("hex");
}

async function sendOfficialClaimCode(reservationId: string, email: string, code: string) {
  const response = await fetch(officialClaimUrl, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${getServerEnv("OFFICIAL_BOOKING_WEBHOOK_SECRET")}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ reservation_id: reservationId, email, code }),
    signal: AbortSignal.timeout(15000),
  }).catch(() => null);
  if (!response?.ok) return null;
  return await response.json().catch(() => null) as { sent?: unknown; booking?: OfficialBooking } | null;
}

function parseOfficialBooking(source: OfficialBooking | undefined) {
  const lessonType = typeof source?.lesson_type === "string" ? source.lesson_type.trim() : "";
  const preferredDate = typeof source?.preferred_date === "string" ? source.preferred_date.trim() : "";
  const preferredTime = typeof source?.preferred_time === "string" ? source.preferred_time.trim() : "";
  const durationMinutes = Number(source?.duration_minutes);
  const startsAt = new Date(`${preferredDate}T${preferredTime}:00+09:00`);
  if (!lessonType || !/^\d{4}-\d{2}-\d{2}$/.test(preferredDate)
    || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(preferredTime) || Number.isNaN(startsAt.getTime())
    || !Number.isInteger(durationMinutes) || durationMinutes <= 0 || durationMinutes > 480) {
    return null;
  }
  return {
    lesson_type: lessonType,
    starts_at: startsAt.toISOString(),
    ends_at: new Date(startsAt.getTime() + durationMinutes * 60_000).toISOString(),
    status: source?.status === "確定" ? "確定" : "予約済み",
  };
}

export async function POST(request: Request) {
  if (!serverConfigReady()) {
    return NextResponse.json({ error: "現在、予約連携を利用できません。" }, { status: 503 });
  }
  const session = await verifyPortalSession((await cookies()).get(sessionCookieName)?.value);
  if (!session) return NextResponse.json({ error: "LINEでログインしてください。" }, { status: 401 });

  const body = (await request.json().catch(() => null)) as { reservation_id?: unknown; email?: unknown } | null;
  const reservationId = typeof body?.reservation_id === "string" ? body.reservation_id.trim().toUpperCase() : "";
  const email = typeof body?.email === "string" ? body.email.trim().toLowerCase() : "";
  if (!/^R-\d{8}-\d{3,}$/.test(reservationId) || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return NextResponse.json({ error: "受付番号とメールアドレスを確認してください。" }, { status: 400 });
  }

  const supabase = createAdminClient();
  const { data: booking, error: bookingError } = await supabase
    .from("lesson_bookings")
    .select("guardian_line_user_id")
    .eq("official_reservation_id", reservationId)
    .maybeSingle();
  if (bookingError) return NextResponse.json({ error: "予約を確認できませんでした。" }, { status: 502 });

  const code = String(randomInt(100000, 1000000));
  let codeAlreadySent = false;
  if (!booking) {
    const officialResult = await sendOfficialClaimCode(reservationId, email, code);
    const officialBooking = officialResult?.sent === true ? parseOfficialBooking(officialResult.booking) : null;
    if (!officialBooking) return NextResponse.json({ accepted: true });

    const { error: insertError } = await supabase.from("lesson_bookings").insert({
      official_reservation_id: reservationId,
      cal_booking_id: null,
      guardian_line_user_id: null,
      ...officialBooking,
      updated_at: new Date().toISOString(),
    });
    if (insertError && insertError.code !== "23505") {
      console.error("Failed to mirror claimed official booking", insertError.code);
      return NextResponse.json({ accepted: true });
    }
    codeAlreadySent = true;
  }

  if (!booking || booking.guardian_line_user_id === null) {
    const { data: challengeCreated, error } = await supabase.rpc("create_booking_claim_challenge", {
      p_official_reservation_id: reservationId,
      p_guardian_line_user_id: session.lineUserId,
      p_code_digest: digest(reservationId, session.lineUserId, code),
    });
    if (error) {
      console.error("Failed to save booking claim challenge", error.code);
    } else if (challengeCreated === true && !codeAlreadySent) {
      await sendOfficialClaimCode(reservationId, email, code);
    }
  }

  return NextResponse.json({ accepted: true });
}