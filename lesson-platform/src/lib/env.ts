const requiredServerVariables = [
  "SUPABASE_URL",
  "SUPABASE_SERVICE_ROLE_KEY",
  "PORTAL_SESSION_SECRET",
  "OFFICIAL_BOOKING_WEBHOOK_SECRET",
] as const;

const minimumSessionSecretBytes = 32;

function validServerVariable(name: (typeof requiredServerVariables)[number]) {
  const value = process.env[name];
  if (!value) return false;
  return name !== "PORTAL_SESSION_SECRET"
    || new TextEncoder().encode(value).byteLength >= minimumSessionSecretBytes;
}

export function serverConfigReady() {
  return requiredServerVariables.every(validServerVariable);
}

export function getServerEnv(name: (typeof requiredServerVariables)[number]) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not configured`);
  if (name === "PORTAL_SESSION_SECRET"
    && new TextEncoder().encode(value).byteLength < minimumSessionSecretBytes) {
    throw new Error(`${name} must contain at least ${minimumSessionSecretBytes} bytes`);
  }
  return value;
}

export const publicSiteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? "https://portal.namegawa-brass-lab.com";

export const legacyLessonUrl = "https://namegawa-brass-lab.com/lesson/";
