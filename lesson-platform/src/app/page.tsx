import { ArrowRight, CalendarCheck, MapPin, MessageCircle, ShieldCheck, Sparkles } from "lucide-react";
import Image from "next/image";

import { BookingPanel } from "@/components/booking-panel";
import { LineLogin } from "@/components/line-login";
import { OfficialAvailability } from "@/components/official-availability";
import { legacyLessonUrl } from "@/lib/env";

export default function Home() {
  const structuredData = {
    "@context": "https://schema.org",
    "@type": "MusicSchool",
    name: "kazooささきトランペット教室",
    url: "https://namegawa-brass-lab.com/lesson/",
    areaServed: ["滑川町", "比企郡", "東松山市", "嵐山町"],
    description: "初心者・学生・吹奏楽部に対応するトランペット教室",
  };

  return (
    <main>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }} />
      <section className="hero">
        <div className="hero-copy">
          <p className="location"><MapPin aria-hidden="true" size={17} /> 埼玉県滑川町・比企郡</p>
          <h1>kazooささき<br />トランペット教室</h1>
          <p className="hero-lead">音が変わる。練習の迷いも変わる。一人ひとりの吹き方と生活に合わせ、予約・変更・出欠確認までスマートフォンで完結します。</p>
          <div className="hero-actions">
            <a className="primary-button" href="#booking">30分無料体験を予約 <ArrowRight aria-hidden="true" size={19} /></a>
            <a className="secondary-button" href={legacyLessonUrl}>現在の教室案内</a>
          </div>
        </div>
        <div className="hero-visual" aria-label="kazooささきトランペット教室ロゴ">
          <div className="hero-brand-lockup">
            <Image className="lesson-logo" src="/trumpet-school-logo.png" alt="" width={800} height={526} priority />
            <Image className="hero-wordmark" src="/namegawa-wordmark.png" alt="なめがわブラス・ラボ" width={1000} height={111} priority />
          </div>
          <p className="hero-school-name"><span>kazooささき</span><strong>トランペット教室</strong></p>
        </div>
      </section>

      <section className="trust-strip" aria-label="サービスの特徴">
        <span><CalendarCheck aria-hidden="true" /> 24時間予約</span>
        <span><MessageCircle aria-hidden="true" /> LINEで確認</span>
        <span><ShieldCheck aria-hidden="true" /> 予定と出欠を一元管理</span>
      </section>

      <section className="value-section" aria-labelledby="value-title">
        <div className="section-heading">
          <p className="kicker">LOCAL & PERSONAL</p>
          <h2 id="value-title">大きな教室にはない、近さと速さ。</h2>
        </div>
        <div className="value-grid">
          <article><span>01</span><h3>地域の予定に合わせる</h3><p>学校、部活動、地域行事を踏まえて、無理なく続けられる時間を選べます。</p></article>
          <article><span>02</span><h3>LINEだけで迷わない</h3><p>保護者の方も新しいアプリを増やさず、予約と次回予定を確認できます。</p></article>
          <article><span>03</span><h3>予定と出欠がすぐ分かる</h3><p>次回日時や予約状況をマイページに集約し、教室との行き違いを減らします。</p></article>
        </div>
      </section>

      <section className="price-section" id="price" aria-labelledby="price-title">
        <div className="section-heading">
          <p className="kicker">LESSON FEE</p>
          <h2 id="price-title">レッスン料金</h2>
          <p>年代に合わせた時間と回数で、一人ひとりを丁寧に指導します。</p>
        </div>
        <div className="price-grid">
          <article className="trial-fee"><h3>体験レッスン</h3><p className="fee">30分 <strong>無料</strong></p><span>初めての方はこちらからお申し込みください</span></article>
          <article><h3>小学生</h3><p className="fee">月2回：<strong>3,000円</strong></p><span>個人レッスン・1回30分</span></article>
          <article><h3>中学生</h3><p className="fee">月2回：<strong>5,000円</strong></p><span>個人レッスン・1回45分</span></article>
          <article><h3>高校生以上・大人</h3><p className="fee">基本料金：1回<strong>7,000円</strong></p><span>個人レッスン・1回60分</span></article>
          <article><h3>その他</h3><p className="fee">グループレッスン・部活動指導</p><span>要相談</span></article>
        </div>
      </section>

      <OfficialAvailability />

      <BookingPanel />

      <section className="login-section" id="login" aria-labelledby="login-title">
        <div>
          <p className="kicker"><Sparkles aria-hidden="true" size={16} /> FOR FAMILIES</p>
          <h2 id="login-title">保護者の方も、LINEですぐ確認。</h2>
          <p>予約履歴、次回日程、出欠状況をひとつの画面にまとめます。専用アプリのインストールは不要です。</p>
        </div>
        <div className="login-control">
          <LineLogin liffId={process.env.NEXT_PUBLIC_LINE_LIFF_ID} />
          <small>ログイン時はLINEの認証画面へ移動します。</small>
        </div>
      </section>
    </main>
  );
}
