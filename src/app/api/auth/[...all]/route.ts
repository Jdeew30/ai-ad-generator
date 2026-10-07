import { toNextJsHandler } from "better-auth/next-js";
import { getAuth } from "@/lib/auth";

// The auth instance is created lazily so that importing this module at build
// time does not require runtime secrets.
const handler = (request: Request) => getAuth().handler(request);

export const { GET, POST } = toNextJsHandler(handler);
