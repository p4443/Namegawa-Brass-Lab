const requiredServerVariables = [
  "SUPABASE_URL",
  "SUPABASE_SERVICE_ROLE_KEY",
  "PORTAL_SESSION_SECRET",
] as const;

export function serverConfigReady() {
  return requiredServerVariables.every((name) => Boolean(process.env[name]));
}

export function getServerEnv(name: (typeof requiredServerVariables)[number]) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not configured`);
  return value;
}

export const publicSiteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://portal.namegawa-brass-lab.com";

export const legacyLessonUrl = "https://namegawa-brass-lab.com/lesson/";
