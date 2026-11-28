// Minimal local stand-ins so this example type-checks WITHOUT installing Next.js.
// Delete this file (and remove it from tsconfig "include") once `next` is installed: the real types replace it.

declare module "next/server" {
  export interface CookieOptions {
    maxAge?: number;
    httpOnly?: boolean;
    secure?: boolean;
    sameSite?: "lax" | "strict" | "none";
    path?: string;
    domain?: string;
  }
  export class NextRequest extends Request {
    readonly cookies: { get(name: string): { name: string; value: string } | undefined };
  }
  /** Run work after the response is sent (Next 15.1+). */
  export function after(task: Promise<unknown> | (() => unknown)): void;
  export class NextResponse<Body = unknown> extends Response {
    readonly cookies: { set(name: string, value: string, options?: CookieOptions): unknown };
    static json<T>(body: T, init?: ResponseInit): NextResponse<T>;
    static next(init?: ResponseInit): NextResponse;
  }
}

// Real package: makes a build fail if a client component imports the module.
declare module "server-only" {}

// Real source: @types/node.
declare const process: { env: Record<string, string | undefined> };

// Real source: @types/node.
declare module "node:net" {
  export function isIP(input: string): 0 | 4 | 6;
}
