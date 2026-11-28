import { Tenkyu } from "tenkyu";

// Server-only module. Never import this from a client component.
export const tenkyu = new Tenkyu({ apiKey: process.env.TENKYU_API_KEY! });
export const GRANT = Number(process.env.SIGNUP_CREDITS ?? 100);
