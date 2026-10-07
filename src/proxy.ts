import { NextResponse, type NextRequest } from "next/server";
import { getSessionCookie } from "better-auth/cookies";

/**
 * Optimistic gate only: redirects visitors that have no session cookie at all.
 * It is NOT an authorisation check — every page, action and route handler still
 * validates the session against the database (see lib/session.ts).
 */
export function proxy(request: NextRequest) {
  const hasSessionCookie = Boolean(getSessionCookie(request));
  if (!hasSessionCookie) {
    const url = new URL("/login", request.url);
    url.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/projects/:path*", "/settings/:path*"],
};
