"use client";

import { FormEvent, useEffect, useState } from "react";
import { CalendarCheck, FileText } from "lucide-react";

const lessonTypes = ["体験レッスン", "小学生", "中学生", "高校生以上・大人"] as const;

function japanDate(offsetDays = 0) {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function oneMonthFromTodayInJapan() {
  const [year, month, day] = japanDate().split("-").map(Number);
  const targetYear = month === 12 ? year + 1 : year;
  const targetMonth = month === 12 ? 1 : month + 1;
  const lastDay = new Date(Date.UTC(targetYear, targetMonth, 0)).getUTCDate();
  return `${targetYear}-${String(targetMonth).padStart(2, "0")}-${String(Math.min(day, lastDay)).padStart(2, "0")}`;
}

function endTime(startTime: string, durationMinutes: number | null) {
  if (!durationMinutes) return startTime;
  const [hours, minutes] = startTime.split(":").map(Number);
  const total = hours * 60 + minutes + durationMinutes;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

export function BookingPanel({ isLineAuthenticated = false }: { isLineAuthenticated?: boolean }) {
  const [lessonType, setLessonType] = useState("");
  const [date, setDate] = useState("");
  const [availableTimes, setAvailableTimes] = useState<string[]>([]);
  const [durationMinutes, setDurationMinutes] = useState<number | null>(null);
  const [availabilityMessage, setAvailabilityMessage] = useState("レッスン種別と希望日を選択してください。");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ state: "success" | "error"; message: string } | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [cancellationResult, setCancellationResult] = useState<{ state: "success" | "error"; message: string } | null>(null);

  useEffect(() => {
    setAvailableTimes([]);
    setDurationMinutes(null);
    if (!lessonType || !date) {
      setAvailabilityMessage("レッスン種別と希望日を選択してください。");
      return;
    }

    const controller = new AbortController();
    setAvailabilityMessage("公式予約台帳の空き枠を確認しています…");
    const params = new URLSearchParams({ date, lesson_type: lessonType });
    fetch(`/api/bookings?${params}`, { signal: controller.signal })
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "空き枠を取得できませんでした。");
        setAvailableTimes(Array.isArray(body.available_times) ? body.available_times : []);
        setDurationMinutes(Number(body.duration_minutes) || null);
        setAvailabilityMessage(body.available_times?.length ? "予約可能な時間を選択してください。" : "この日の予約可能枠はありません。");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setAvailabilityMessage(error instanceof Error ? error.message : "空き枠を取得できませんでした。");
      });

    return () => controller.abort();
  }, [date, lessonType]);

  async function submitBooking(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setResult(null);
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form));

    try {
      const response = await fetch("/api/bookings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok || body.saved !== true) throw new Error(body.error || "予約を受け付けられませんでした。");
      const bookingStatus = body.status === "確定" ? "確定" : "確認中";
      const receiptNotice = body.auto_reply_sent === true ? "受付メールを送信しました。" : "受付内容は公式予約台帳に登録されています。";
      setResult({
        state: "success",
        message: `予約を受け付けました（${bookingStatus}）。受付番号は ${body.reservation_id} です。${receiptNotice}確定時にもメールでお知らせします。以後の予定はLINEメニューの「予定確認」から確認できます。`,
      });
      form.reset();
      setLessonType("");
      setDate("");
      setAvailableTimes([]);
      setDurationMinutes(null);
      setAvailabilityMessage("レッスン種別と希望日を選択してください。");
    } catch (error) {
      setResult({ state: "error", message: error instanceof Error ? error.message : "予約を受け付けられませんでした。" });
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelBooking(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCancelling(true);
    setCancellationResult(null);
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form));

    try {
      const response = await fetch("/api/bookings", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok || body.cancelled !== true) throw new Error(body.error || "予約をキャンセルできませんでした。");
      setCancellationResult({ state: "success", message: body.already_cancelled ? "この予約はすでにキャンセルされています。" : "予約をキャンセルしました。" });
      form.reset();
    } catch (error) {
      setCancellationResult({ state: "error", message: error instanceof Error ? error.message : "予約をキャンセルできませんでした。" });
    } finally {
      setCancelling(false);
    }
  }

  return (
    <section className="booking-section" id="booking" aria-labelledby="booking-title">
      <div className="booking-heading">
        <p className="kicker"><CalendarCheck aria-hidden="true" size={16} /> 公式予約台帳と連動</p>
        <h2 id="booking-title">レッスンを予約する</h2>
        <p>空き枠の確認から予約申込まで、この画面で完了します。受け付けた内容は確認中として公式予約台帳へ即時反映されます。</p>
      </div>
      <form className="booking-form" onSubmit={submitBooking}>
        <label>お名前<span>必須</span><input name="name" autoComplete="name" maxLength={80} required /></label>
        <label>メールアドレス<span>必須</span><input name="email" type="email" autoComplete="email" required /></label>
        <label>電話番号<input name="phone" type="tel" autoComplete="tel" inputMode="tel" /></label>
        <label>レッスン種別<span>必須</span>
          <select name="lesson_type" value={lessonType} onChange={(event) => setLessonType(event.target.value)} required>
            <option value="">選択してください</option>
            {lessonTypes.map((value) => <option key={value} value={value}>{value === "体験レッスン" ? "無料体験レッスン" : value}</option>)}
          </select>
        </label>
        <label>希望日<span>必須</span><input name="preferred_date" type="date" min={japanDate(1)} max={oneMonthFromTodayInJapan()} value={date} onChange={(event) => setDate(event.target.value)} required /></label>
        <label>希望時間<span>必須</span>
          <select name="preferred_time" disabled={!availableTimes.length} required>
            <option value="">{availableTimes.length ? "選択してください" : "空き枠を確認してください"}</option>
            {availableTimes.map((time) => <option key={time} value={time}>{time}〜{endTime(time, durationMinutes)}{durationMinutes ? `（${durationMinutes}分）` : ""}</option>)}
          </select>
        </label>
        <p className="availability-message" role="status">{availabilityMessage}</p>
        <label className="booking-message">ご要望・経験年数など<textarea name="message" maxLength={500} /></label>
        <label className="booking-consent"><input name="privacy_agreed" type="checkbox" required /><span><a href="https://namegawa-brass-lab.com/legal/privacy-policy.html" target="_blank" rel="noreferrer">プライバシーポリシー</a>を確認し、個人情報の取り扱いに同意します。</span></label>
        <label className="booking-trap" aria-hidden="true">ウェブサイト<input name="website" tabIndex={-1} autoComplete="off" /></label>
        <div className="booking-submit">
          <button className="primary-button" type="submit" disabled={!isLineAuthenticated || submitting || !availableTimes.length}>{submitting ? "予約を送信中…" : isLineAuthenticated ? "予約を申し込む" : "LINEログイン後に予約"}</button>
          <a className="application-link" href="https://namegawa-brass-lab.com/lesson/application-form.html" target="_blank" rel="noreferrer"><FileText aria-hidden="true" size={17} />受講申込書を表示・印刷</a>
        </div>
        {!isLineAuthenticated && <p className="booking-result" data-state="error">予約状況をLINEと同期するため、先に<a href="#login">LINEでログイン</a>してください。</p>}
        {result && <p className="booking-result" data-state={result.state} role="status">{result.message}</p>}
      </form>
      <details className="booking-cancel">
        <summary>予約をキャンセルする</summary>
        <form onSubmit={cancelBooking}>
          <label>受付番号<input name="reservation_id" placeholder="R-20260926-001" pattern="R-\d{8}-\d{3,}" required /></label>
          <label>予約時のメールアドレス<input name="email" type="email" autoComplete="email" required /></label>
          <button className="secondary-button" type="submit" disabled={cancelling}>{cancelling ? "キャンセル処理中…" : "予約をキャンセル"}</button>
          {cancellationResult && <p className="booking-result" data-state={cancellationResult.state} role="status">{cancellationResult.message}</p>}
        </form>
      </details>
    </section>
  );
}
