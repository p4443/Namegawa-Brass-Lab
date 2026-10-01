type LineVerification = {
  sub?: string;
  name?: string;
  aud?: string;
  exp?: number;
};

export async function verifyLineIdToken(idToken: string) {
  const clientId = process.env.LINE_CHANNEL_ID;
  if (!clientId) throw new Error("LINE_CHANNEL_ID is not configured");

  const body = new URLSearchParams({ id_token: idToken, client_id: clientId });
  const response = await fetch("https://api.line.me/oauth2/v2.1/verify", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
    cache: "no-store",
  });
  if (!response.ok) return null;

  const profile = (await response.json()) as LineVerification;
  if (!profile.sub || profile.aud !== clientId || !profile.exp || profile.exp * 1000 <= Date.now()) {
    return null;
  }
  return { lineUserId: profile.sub, displayName: profile.name?.slice(0, 80) || "LINE利用者" };
}
