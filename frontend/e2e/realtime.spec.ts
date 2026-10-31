import { expect, test } from "@playwright/test";

import { DEMO, login } from "./support/auth";

/**
 * The browser's live feed.
 *
 * The socket is the same one the shell uses (`src/lib/realtime.ts`): the access token travels as a
 * query parameter, the server greets with `connected` and answers `ping` with `pong`. Business-event
 * fan-out across replicas is proven separately by `scripts/e2e_journeys.py`, which runs two API
 * processes against one Redis and asserts the event reaches a socket on each.
 */
test.describe("live updates", () => {
  test("the session token opens an authenticated socket that answers a heartbeat", async ({ page }) => {
    await login(page, DEMO.manager);

    const token = await page.evaluate(() => sessionStorage.getItem("procureai.access"));
    expect(token, "the access token is kept in sessionStorage").toBeTruthy();

    const transcript = await page.evaluate(
      (value) =>
        new Promise<string[]>((resolve) => {
          const seen: string[] = [];
          const scheme = location.protocol === "https:" ? "wss" : "ws";
          const socket = new WebSocket(`${scheme}://${location.host}/ws?token=${encodeURIComponent(value ?? "")}`);
          const done = () => {
            socket.close();
            resolve(seen);
          };
          socket.onmessage = (event) => {
            const frame = JSON.parse(String(event.data)) as { type?: string };
            seen.push(String(frame.type));
            if (frame.type === "connected") socket.send("ping");
            if (frame.type === "pong") done();
          };
          socket.onerror = done;
          setTimeout(done, 15_000);
        }),
      token,
    );

    expect(transcript).toContain("connected");
    expect(transcript).toContain("pong");
  });

  test("an invalid token is closed by the server instead of joining the room", async ({ page }) => {
    await login(page, DEMO.manager);

    const code = await page.evaluate(
      () =>
        new Promise<number>((resolve) => {
          const scheme = location.protocol === "https:" ? "wss" : "ws";
          const socket = new WebSocket(`${scheme}://${location.host}/ws?token=not-a-token`);
          socket.onclose = (event) => resolve(event.code);
          setTimeout(() => resolve(0), 15_000);
        }),
    );

    expect(code).toBe(4401);
  });
});
