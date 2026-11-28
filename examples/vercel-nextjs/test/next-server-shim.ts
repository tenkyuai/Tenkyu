// Minimal stand-in for next/server so the recipe's tests run without installing Next.js.
export class NextRequest extends Request {
  get cookies() {
    const jar = new Map<string, string>();
    for (const part of (this.headers.get("cookie") ?? "").split(";")) {
      const i = part.indexOf("=");
      if (i > 0) jar.set(part.slice(0, i).trim(), part.slice(i + 1).trim());
    }
    return { get: (name: string) => (jar.has(name) ? { name, value: jar.get(name) as string } : undefined) };
  }
}
export class NextResponse extends Response {
  static override json(body: unknown, init?: ResponseInit) {
    const headers = new Headers(init?.headers);
    headers.set("content-type", "application/json");
    return new NextResponse(JSON.stringify(body), { ...init, headers });
  }
}
// Next 15.1+: run work after the response. The stand-in just starts it; failures are the caller's to catch.
export function after(task: Promise<unknown> | (() => unknown)): void {
  void Promise.resolve(typeof task === "function" ? task() : task).catch(() => {});
}
