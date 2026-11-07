"use client";

import Script from "next/script";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { LogIn } from "lucide-react";

interface LiffApi {
  init(config: { liffId: string }): Promise<void>;
  isLoggedIn(): boolean;
  isInClient(): boolean;
  login(config?: { redirectUri?: string }): void;
  getIDToken(): string | null;
}

declare global {
  interface Window { liff?: LiffApi }
}

export function LineLogin({ liffId }: { liffId?: string }) {
  const router = useRouter();
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
        // LINEアプリ内の通常ブラウザではaccess.line.meの自動ログインが失敗することがあるため、
        // LIFFブラウザで開き直してリダイレクト無しの自動ログインに切り替える
        if (/\bLine\//i.test(navigator.userAgent) && !window.liff.isInClient()) {
          window.location.replace(`https://liff.line.me/${liffId}?login=required`);
          return;
        }
        window.liff.login({ redirectUri: window.location.href });
        return;
      }
      const idToken = window.liff.getIDToken();
      if (!idToken) throw new Error("LINE ID token is unavailable");
      const response = await fetch("/api/auth/line", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idToken }),
        // 応答が滞ってもボタンが「確認中...」のまま固まらないようにする
        signal: AbortSignal.timeout(20000),
      });
      const result = (await response.json()) as { error?: string };
      if (!response.ok) throw new Error(result.error || "ログインできませんでした。");
      sessionStorage.removeItem("nbl_auto_login_attempts");
      router.push("/dashboard");
    } catch (error) {
      setStatus(
        error instanceof DOMException && error.name === "TimeoutError"
          ? "応答がありません。通信環境を確認して、もう一度お試しください。"
          : error instanceof Error ? error.message : "ログインできませんでした。",
      );
      setBusy(false);
      loginStarted.current = false;
    }
  }

  function continueLoginIfNeeded() {
    const params = new URLSearchParams(window.location.search);
    // LINE認証からの戻り(code/state/liffClientId)やLIFF起動時も自動でログインを続行する
    const returningFromLineAuth =
      params.has("liff.state")
      || params.has("liffClientId")
      || (params.has("code") && params.has("state"));
    if (returningFromLineAuth) {
      void login();
      return;
    }
    if (params.get("login") !== "required") return;
    // マイページから差し戻され続ける場合は自動再試行を打ち切り、案内を表示する
    const attempts = Number(sessionStorage.getItem("nbl_auto_login_attempts") || "0");
    if (attempts >= 2) {
      setStatus("ログイン状態を保存できませんでした。ブラウザのCookie設定を確認し、もう一度「LINEでログイン」を押してください。");
      return;
    }
    sessionStorage.setItem("nbl_auto_login_attempts", String(attempts + 1));
    void login();
  }

  return (
    <>
      <Script
        src="https://static.line-scdn.net/liff/edge/2/sdk.js"
        strategy="afterInteractive"
        onLoad={continueLoginIfNeeded}
        onReady={continueLoginIfNeeded}
      />
      <button className="line-button" type="button" onClick={login} disabled={busy}>
        <LogIn aria-hidden="true" size={20} />
        {busy ? "確認中..." : "LINEでログイン"}
      </button>
      <p className="form-status" role="status" aria-live="polite">{status}</p>
    </>
  );
}
