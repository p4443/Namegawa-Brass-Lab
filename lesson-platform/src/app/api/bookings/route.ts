import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

import { serverConfigReady } from "@/lib/env";
import { sessionCookieName, verifyPortalSession } from "@/lib/session";
import { createAdminClient } from "@/lib/supabase";

const officialApiBaseUrl = "https://namegawa-brass-lab.com/api";
const lessonTypes = new Set(["体験レッスン", "小学生", "中学生", "高校生以上・大人"]);
const datePattern = /^\d{4}-\d{2}-\d{2}$/;
const timePattern = /^\d{2}:\d{2}$/;

export const maxDuration = 120;

type BookingRequest = {
  name?: unknown;
  email?: unknown;
  phone?: unknown;
  lesson_type?: unknown;
  preferred_date?: unknown;
  preferred_time?: unknown;
  message?: unknown;
  website?: unknown;
};

type CancellationRequest = {
  reservation_id?: unknown;
  email?: unknown;
};

function text(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function officialUrl(path: string) {
  return new URL(`${officialApiBaseUrl}${path}`);
}

function bookingDateRange() {
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const [year, month, day] = today.split("-").map(Number);
  const targetYear = month === 12 ? year + 1 : year;
  const targetMonth = month === 12 ? 1 : month + 1;
  const lastDay = new Date(Date.UTC(targetYear, targetMonth, 0)).getUTCDate();
  const first = new Date(`${today}T00:00:00+09:00`);
  first.setUTCDate(first.getUTCDate() + 1);
  return {
    first: new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit" }).format(first),
    last: `${targetYear}-${String(targetMonth).padStart(2, "0")}-${String(Math.min(day, lastDay)).padStart(2, "0")}`,
  };
}

function isBookableDate(value: string) {
  const range = bookingDateRange();
  return datePattern.test(value) && value >= range.first && value <= range.last;
}

async function fetchOfficialAvailability(url: URL) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(10000) });
      if (response.ok) return response;
    } catch {
      if (attempt === 2) return null;
    }
    if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 500 * (attempt + 1)));
  }
  return null;
}

export async function GET(request: NextRequest) {
  const date = request.nextUrl.searchParams.get("date") ?? "";
  const lessonType = request.nextUrl.searchParams.get("lesson_type") ?? "";
  if (!isBookableDate(date) || !lessonTypes.has(lessonType)) {
    return NextResponse.json({ error: "希望日とレッスン種別を確認してください。" }, { status: 400 });
  }

  const url = officialUrl("/lesson-calendar");
  url.searchParams.set("from", date);
  url.searchParams.set("to", date);

  try {
    const response = await fetchOfficialAvailability(url);
    if (!response) {
      return NextResponse.json({ error: "現在、空き枠を取得できません。" }, { status: 502 });
    }

    const result = await response.json() as {
      days?: Array<{ lessons?: Record<string, { duration_minutes?: number; available_times?: string[] }> }>;
    };
    const lesson = result.days?.[0]?.lessons?.[lessonType];
    return NextResponse.json({
      duration_minutes: Number(lesson?.duration_minutes) || null,
      available_times: Array.isArray(lesson?.available_times)
        ? lesson.available_times.filter((value) => timePattern.test(value))
        : [],
    });
  } catch {
    return NextResponse.json({ error: "現在、空き枠を取得できません。" }, { status: 502 });
  }
}

export async function POST(request: NextRequest) {
  let body: BookingRequest;
  try {
    body = await request.json() as BookingRequest;
  } catch {
    return NextResponse.json({ error: "入力内容を確認してください。" }, { status: 400 });
  }

  const payload = {
    name: text(body.name),
    email: text(body.email),
    phone: text(body.phone),
    lesson_type: text(body.lesson_type),
    preferred_date: text(body.preferred_date),
    preferred_time: text(body.preferred_time),
    message: text(body.message),
    website: text(body.website),
  };

  if (!lessonTypes.has(payload.lesson_type) || !isBookableDate(payload.preferred_date) || !timePattern.test(payload.preferred_time)) {
    return NextResponse.json({ error: "レッスン種別と希望日時を確認してください。" }, { status: 400 });
  }

  try {
    const forwardedFor = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
    const response = await fetch(officialUrl("/lesson-reservations"), {
      method: "POST",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...(forwardedFor ? { "X-Forwarded-For": forwardedFor } : {}),
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(90000),
    });
    const result = await response.json() as Record<string, unknown>;

    if (!response.ok || result.saved !== true) {
      return NextResponse.json(result, { status: response.status });
    }

    const reservationId = text(result.reservation_id);
    const durationMinutes = Number(result.duration_minutes) || 0;
    if (reservationId && durationMinutes && serverConfigReady()) {
      const cookieStore = await cookies();
      const session = await verifyPortalSession(cookieStore.get(sessionCookieName)?.value);
      if (session) {
        const startsAt = new Date(`${payload.preferred_date}T${payload.preferred_time}:00+09:00`);
        const endsAt = new Date(startsAt.getTime() + durationMinutes * 60_000);
        const { error } = await createAdminClient().from("lesson_bookings").upsert({
          official_reservation_id: reservationId,
          cal_booking_id: null,
          guardian_line_user_id: session.lineUserId,
          lesson_type: payload.lesson_type,
          starts_at: startsAt.toISOString(),
          ends_at: endsAt.toISOString(),
          status: "予約済み",
          updated_at: new Date().toISOString(),
        }, { onConflict: "official_reservation_id" });
        if (error) console.error("Failed to mirror official lesson booking", error.code);
      }
    }

    return NextResponse.json({
      saved: true,
      reservation_id: reservationId,
      status: text(result.status),
      duration_minutes: durationMinutes,
      auto_reply_sent: result.auto_reply_sent === true,
      duplicate: result.duplicate === true,
    }, { status: 201 });
  } catch {
    return NextResponse.json({
      error: "予約結果を確認できませんでした。重複防止のため再送せず、受付メールまたはLINEメニューの「予定確認」をご確認ください。",
    }, { status: 502 });
  }
}

export async function DELETE(request: NextRequest) {
  let body: CancellationRequest;
  try {
    body = await request.json() as CancellationRequest;
  } catch {
    return NextResponse.json({ error: "入力内容を確認してください。" }, { status: 400 });
  }

  const reservationId = text(body.reservation_id).toUpperCase();
  const email = text(body.email).toLowerCase();
  if (!/^R-\d{8}-\d{3,}$/.test(reservationId) || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return NextResponse.json({ error: "受付番号とメールアドレスを確認してください。" }, { status: 400 });
  }

  try {
    const response = await fetch(officialUrl("/lesson-reservations/cancel"), {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reservation_id: reservationId, email }),
      signal: AbortSignal.timeout(15000),
    });
    const result = await response.json() as Record<string, unknown>;
    if (!response.ok || result.cancelled !== true) {
      return NextResponse.json(result, { status: response.status });
    }

    if (serverConfigReady()) {
      const cookieStore = await cookies();
      const session = await verifyPortalSession(cookieStore.get(sessionCookieName)?.value);
      if (session) {
        const { error } = await createAdminClient()
          .from("lesson_bookings")
          .update({ status: "キャンセル", updated_at: new Date().toISOString() })
          .eq("official_reservation_id", reservationId)
          .eq("guardian_line_user_id", session.lineUserId);
        if (error) console.error("Failed to mirror official lesson cancellation", error.code);
      }
    }

    return NextResponse.json({
      cancelled: true,
      reservation_id: text(result.reservation_id) || reservationId,
      already_cancelled: result.already_cancelled === true,
    });
  } catch {
    return NextResponse.json({ error: "予約をキャンセルできませんでした。時間をおいて再度お試しください。" }, { status: 502 });
  }
}
