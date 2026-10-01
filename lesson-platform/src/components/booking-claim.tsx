"use client";

import { FormEvent, useState } from "react";
import { Link2 } from "lucide-react";
import { useRouter } from "next/navigation";

type Result = { state: "success" | "error"; message: string };

export function BookingClaim() {
  const router = useRouter();
  const [reservationId, setReservationId] = useState("");
  const [codeRequested, setCodeRequested] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<Result | null>(null);

  async function requestCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setResult(null);
    const payload = Object.fromEntries(new FormData(event.currentTarget));
    try {
      const response = await fetch("/api/bookings/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "確認コードを送信できませんでした。");
      setReservationId(String(payload.reservation_id).trim().toUpperCase());
      setCodeRequested(true);
      setResult({ state: "success", message: "予約情報が一致する場合、予約時のメールアドレスへ確認コードを送信しました。" });
    } catch (error) {
      setResult({ state: "error", message: error instanceof Error ? error.message : "確認コードを送信できませんでした。" });
    } finally {
      setSubmitting(false);
    }
  }

  async function verifyCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setResult(null);
    const code = String(new FormData(event.currentTarget).get("code") || "");
    try {
      const response = await fetch("/api/bookings/claim/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reservation_id: reservationId, code }),
      });
      const body = await response.json();
      if (!response.ok || body.linked !== true) throw new Error(body.error || "予約を追加できませんでした。");
      setCodeRequested(false);
      setResult({ state: "success", message: "予約を予定確認へ追加しました。" });
      router.refresh();
    } catch (error) {
      setResult({ state: "error", message: error instanceof Error ? error.message : "予約を追加できませんでした。" });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <details className="booking-claim">
      <summary><Link2 aria-hidden="true" size={17} />公式サイトで予約済みの方</summary>
      {!codeRequested ? (
        <form onSubmit={requestCode}>
          <label>受付番号<input name="reservation_id" placeholder="R-20260926-001" pattern="R-\d{8}-\d{3,}" required /></label>
          <label>予約時のメールアドレス<input name="email" type="email" autoComplete="email" required /></label>
          <button className="secondary-button" type="submit" disabled={submitting}>{submitting ? "送信中…" : "確認コードを送信"}</button>
        </form>
      ) : (
        <form onSubmit={verifyCode}>
          <p className="claim-reservation-id">受付番号: <strong>{reservationId}</strong></p>
          <label>メールに届いた6桁の確認コード<input name="code" inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" maxLength={6} required /></label>
          <button className="secondary-button" type="submit" disabled={submitting}>{submitting ? "確認中…" : "予約を追加"}</button>
        </form>
      )}
      {result && <p className="booking-result" data-state={result.state} role="status">{result.message}</p>}
    </details>
  );
}
