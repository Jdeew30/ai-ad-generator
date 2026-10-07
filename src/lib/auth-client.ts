"use client";

import { createAuthClient } from "better-auth/react";

// Same-origin: baseURL is inferred from window.location.
export const authClient = createAuthClient();
