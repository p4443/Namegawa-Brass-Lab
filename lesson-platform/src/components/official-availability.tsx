import { CalendarSync } from "lucide-react";

import { getOfficialLessonAvailability, lessonTypes } from "@/lib/official-lesson-availability";

const shortLessonNames = {
  "体験レッスン": "体験",
  "小学生": "小学生",
  "中学生": "中学生",
  "高校生以上・大人": "高校生以上",
} as const;

const dateFormatter = new Intl.DateTimeFormat("ja-JP", {
  month: "numeric",
  day: "numeric",
  weekday: "short",
  timeZone: "Asia/Tokyo",
});

function availabilityLabel(count: number) {
  if (count === 0) return "受付終了";
  if (count <= 2) return "残りわずか";
  return "予約可";
}

export async function OfficialAvailability() {
  const days = await getOfficialLessonAvailability();

  return (
    <section className="availability-section" aria-labelledby="availability-title">
      <div className="availability-heading">
        <p className="kicker"><CalendarSync aria-hidden="true" size={16} /> 公式予約情報と同期</p>
        <h2 id="availability-title">1か月先までの予約状況</h2>
        <p>公式ホームページの予約件数と空き枠を自動反映しています。予約者の個人情報は表示しません。</p>
      </div>
      {days ? (
        <div className="availability-grid">
          {days.map((day) => (
            <article key={day.date}>
              <div className="availability-date">
                <time dateTime={day.date}>{dateFormatter.format(new Date(`${day.date}T00:00:00+09:00`))}</time>
                <span>{Object.values(day.availableCounts).some((count) => count > 0) ? "予約受付中" : "受付終了"}</span>
              </div>
              <dl>
                {lessonTypes.map((lessonType) => (
                  <div key={lessonType}>
                    <dt>{shortLessonNames[lessonType]}</dt>
                    <dd>{availabilityLabel(day.availableCounts[lessonType])}</dd>
                  </div>
                ))}
              </dl>
            </article>
          ))}
        </div>
      ) : (
        <p className="availability-unavailable" role="status">現在、公式予約情報を取得できません。時間をおいて再度ご確認ください。</p>
      )}
      <p className="availability-note">表示は予約できる開始時刻の選択肢をもとにした目安です。教室の生徒数や稼働率を示すものではなく、1件の予約で前後の候補も自動調整されます。</p>
    </section>
  );
}
