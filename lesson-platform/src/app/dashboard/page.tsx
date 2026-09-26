import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { CalendarDays, CircleCheck, Clock3, LogOut } from "lucide-react";

import { logoutAction } from "@/app/actions";
import { BookingPanel } from "@/components/booking-panel";
import { serverConfigReady } from "@/lib/env";
import { sessionCookieName, verifyPortalSession } from "@/lib/session";
import { createAdminClient } from "@/lib/supabase";

export const metadata = { title: "マイページ" };

type Lesson = { id: string; starts_at: string; status: string; lesson_type: string };

export default async function DashboardPage() {
  if (!serverConfigReady()) redirect("/?login=unavailable#login");
  const cookieStore = await cookies();
  const session = await verifyPortalSession(cookieStore.get(sessionCookieName)?.value);
  if (!session) redirect("/?login=required#login");

  const supabase = createAdminClient();
  const { data } = await supabase
    .from("lesson_bookings")
    .select("id, starts_at, status, lesson_type")
    .eq("guardian_line_user_id", session.lineUserId)
    .gte("starts_at", new Date().toISOString())
    .order("starts_at", { ascending: true })
    .limit(10);
  const lessons = (data ?? []) as Lesson[];

  return (
    <main className="dashboard">
      <header className="dashboard-heading">
        <div><p className="kicker">MY LESSONS</p><h1>{session.displayName}さんの予定</h1></div>
        <form action={logoutAction}><button className="icon-text-button" type="submit"><LogOut aria-hidden="true" size={18} />ログアウト</button></form>
      </header>

      <section className="next-lessons" aria-labelledby="next-title">
        <div className="section-heading"><h2 id="next-title">これからのレッスン</h2><p>公式予約台帳で確定した予約が表示されます。</p></div>
        {lessons.length ? (
          <ul className="lesson-list">
            {lessons.map((lesson) => (
              <li key={lesson.id}>
                <span className="lesson-icon"><CalendarDays aria-hidden="true" /></span>
                <div><strong>{lesson.lesson_type}</strong><time dateTime={lesson.starts_at}>{new Intl.DateTimeFormat("ja-JP", { dateStyle: "long", timeStyle: "short", timeZone: "Asia/Tokyo" }).format(new Date(lesson.starts_at))}</time></div>
                <span className="status"><CircleCheck aria-hidden="true" size={16} />{lesson.status}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="empty-state"><Clock3 aria-hidden="true" /><h3>予約中のレッスンはありません</h3><p>空き枠から次のレッスンを選べます。</p></div>
        )}
      </section>
      <BookingPanel isLineAuthenticated />
    </main>
  );
}
