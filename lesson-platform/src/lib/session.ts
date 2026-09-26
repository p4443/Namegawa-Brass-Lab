import { SignJWT, jwtVerify } from "jose";

import { getServerEnv } from "@/lib/env";

export const sessionCookieName = "nbl_portal_session";

export type PortalSession = {
  lineUserId: string;
  displayName: string;
};

function sessionKey() {
  return new TextEncoder().encode(getServerEnv("PORTAL_SESSION_SECRET"));
}

export async function createPortalSession(session: PortalSession) {
  return new SignJWT({ displayName: session.displayName })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(session.lineUserId)
    .setIssuedAt()
    .setExpirationTime("7d")
    .sign(sessionKey());
}

export async function verifyPortalSession(token?: string) {
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, sessionKey(), { algorithms: ["HS256"] });
    if (!payload.sub || typeof payload.displayName !== "string") return null;
    return { lineUserId: payload.sub, displayName: payload.displayName } satisfies PortalSession;
  } catch {
    return null;
  }
}
