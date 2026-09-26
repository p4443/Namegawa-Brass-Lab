import type { Metadata } from "next";
import { BIZ_UDPMincho, Noto_Sans_JP } from "next/font/google";
import Image from "next/image";
import Link from "next/link";

import "./globals.css";

const sans = Noto_Sans_JP({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mincho = BIZ_UDPMincho({ weight: ["400", "700"], subsets: ["latin"], variable: "--font-display", display: "swap" });

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://portal.namegawa-brass-lab.com"),
  title: { default: "kazooささきトランペット教室 | 埼玉県滑川町", template: "%s | kazooささきトランペット教室" },
  description: "埼玉県滑川町・比企郡のkazooささきトランペット教室。LINEで空き枠確認、予約変更、次回予定の確認まで完結。初心者・学生・吹奏楽部を個別に支援します。",
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    locale: "ja_JP",
    title: "kazooささきトランペット教室",
    description: "滑川町・比企郡で、続けやすさを設計したトランペットレッスン。",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body className={`${sans.variable} ${mincho.variable}`}>
        <header className="site-header">
          <Link className="brand" href="/" aria-label="kazooささきトランペット教室 ホーム">
            <Image className="site-brand-logo" src="/site-logo.png" alt="なめがわブラス・ラボ" width={1000} height={504} priority />
          </Link>
          <nav aria-label="メインナビゲーション">
            <a href="https://namegawa-brass-lab.com/lesson/">教室案内</a>
            <a href="/#price">料金</a>
            <a href="/#booking">予約</a>
            <Link className="nav-login" href="/dashboard">マイページ</Link>
          </nav>
        </header>
        {children}
        <footer className="site-footer">
          <p>kazooささきトランペット教室</p>
          <p>埼玉県滑川町・比企郡／運営 なめがわブラス・ラボ</p>
          <div className="footer-links">
            <a href="https://namegawa-brass-lab.com/">公式ホームページ</a>
            <a href="https://namegawa-brass-lab.com/legal/privacy-policy.html">プライバシーポリシー</a>
          </div>
        </footer>
      </body>
    </html>
  );
}
