import "server-only";

const officialSiteUrl = "https://namegawa-brass-lab.com";
const lessonTypes = ["体験レッスン", "小学生", "中学生", "高校生以上・大人"] as const;

export type LessonType = (typeof lessonTypes)[number];

type CalendarResponse = {
  days?: Array<{
    date?: string;
    lessons?: Partial<Record<LessonType, { available_count?: number }>>;
  }>;
};

async function fetchOfficial(url: URL) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(url, {
        next: { revalidate: 300 },
        signal: AbortSignal.timeout(15000),
      });

      if (response.ok) return response;
    } catch {}

    if (attempt === 0) {
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }

  return null;
}

export type OfficialAvailabilityDay = {
  date: string;
  availableCounts: Record<LessonType, number>;
};

function formatJapanDate(date: Date) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export async function getOfficialLessonAvailability(): Promise<OfficialAvailabilityDay[] | null> {
  const today = formatJapanDate(new Date());
  const [year, month, day] = today.split("-").map(Number);
  const targetYear = month === 12 ? year + 1 : year;
  const targetMonth = month === 12 ? 1 : month + 1;
  const lastDay = new Date(Date.UTC(targetYear, targetMonth, 0)).getUTCDate();
  const fromDate = new Date(`${today}T00:00:00+09:00`);
  fromDate.setUTCDate(fromDate.getUTCDate() + 1);

  const from = formatJapanDate(fromDate);
  const to = `${targetYear}-${String(targetMonth).padStart(2, "0")}-${String(Math.min(day, lastDay)).padStart(2, "0")}`;
  const calendarUrl = new URL("/api/lesson-calendar", officialSiteUrl);
  calendarUrl.searchParams.set("from", from);
  calendarUrl.searchParams.set("to", to);

  try {
    const calendarResponse = await fetchOfficial(calendarUrl);
    if (!calendarResponse) return null;

    const calendar = await calendarResponse.json() as CalendarResponse;

    return (calendar.days ?? []).flatMap((day) => {
      if (!day.date) return [];

      return [{
        date: day.date,
        availableCounts: Object.fromEntries(
          lessonTypes.map((lessonType) => [
            lessonType,
            Math.max(0, Number(day.lessons?.[lessonType]?.available_count) || 0),
          ]),
        ) as Record<LessonType, number>,
      }];
    });
  } catch {
    return null;
  }
}

export { lessonTypes };
