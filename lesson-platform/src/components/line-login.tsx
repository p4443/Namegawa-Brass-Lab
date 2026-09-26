"use client";

import Script from "next/script";
import { useRef, useState } from "react";
import { LogIn } from "lucide-react";

interface LiffApi {
  init(config: { liffId: string }): Promise<void>;
  isLoggedIn(): boolean;
  login(config?: { redirectUri?: string }): void;
  getIDToken(): string | null;
}

declare global {
  interface Window { liff?: LiffApi }
}

export function LineLogin({ liffId }: { liffId?: string }) {
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const loginStarted = useRef(false);

  async function login() {
    if (loginStarted.current) return;
    if (!liffId || !window.liff) {
      setStatus("LINEログインは現在準備中です。ページ内の予約フォームをご利用ください。");
      return;
    }
    loginStarted.current = true;
    setBusy(true);
    try {
      await window.liff.init({ liffId });
      if (!window.liff.isLoggedIn()) {
        window.liff.login({ redirectUri: window.location.href });
        return;
      }
      const idToken = window.liff.getIDToken();
      if (!idToken) throw new Error("LINE ID token is unavailable");
      const response = await fetch("/api/auth/line", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idToken }),
      });
      const result = (await response.json()) as { error?: string };
      if (!response.ok) throw new Error(result.error || "ログインできませんでした。");
      window.location.assign("/dashboard");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "ログインできませんでした。");
      setBusy(false);
      loginStarted.current = false;
    }
  }

  function continueLoginIfNeeded() {
    const params = new URLSearchParams(window.location.search);
    if (params.get("login") === "required" || params.has("liff.state")) void login();
  }

  return (
    <>
      <Script src="https://static.line-scdn.net/liff/edge/2/sdk.js" strategy="afterInteractive" onReady={continueLoginIfNeeded} />
      <button className="line-button" type="button" onClick={login} disabled={busy}>
        <LogIn aria-hidden="true" size={20} />
        {busy ? "確認中..." : "LINEでログイン"}
      </button>
      <p className="form-status" role="status" aria-live="polite">{status}</p>
    </>
  );
}
