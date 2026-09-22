// Verifies two real users messaging each other through the actual UI.
// No scripted/bot conversation — this only performs the actions a person
// would: register, pick a contact, type a message, press Send.
export default async function run(page) {
  const stamp = Date.now().toString().slice(-6);
  const check = (ok, label, extra) => ({ label, ok, ...(extra ? { extra } : {}) });
  const results = [];

  // --- User 1 registers ---
  await page.click("#tabRegister");
  await page.fill("#username", `real_a_${stamp}`);
  await page.fill("#displayName", "User One");
  await page.fill("#password", "secret123");
  await page.click("#authSubmit");
  await page.waitForSelector("#app", { state: "visible", timeout: 8000 });

  // --- User 2 registers, in a separate browser context (own cookie jar) ---
  const ctx2 = await page.context().browser().newContext();
  const user2 = await ctx2.newPage();
  await user2.goto(page.url());
  await user2.waitForSelector("#authScreen", { state: "visible" });
  await user2.click("#tabRegister");
  await user2.fill("#username", `real_b_${stamp}`);
  await user2.fill("#displayName", "User Two");
  await user2.fill("#password", "secret123");
  await user2.click("#authSubmit");
  await user2.waitForSelector("#app", { state: "visible", timeout: 8000 });

  // User 1 should see User 2 appear without reloading (live user_joined).
  results.push(check(true, "user 1 signed up", { stamp }));
  results.push(check(true, "user 2 signed up"));

  // --- Each picks the other from their own sidebar ---
  await page.waitForSelector(".conversation", { timeout: 10000 });
  const u1HasU2 = await page
    .locator(".conversation", { hasText: "User Two" })
    .count();
  results.push(check(u1HasU2 > 0, "user 2 appears in user 1's list live"));

  await page.locator(".conversation", { hasText: "User Two" }).first().click();
  await page.waitForSelector("#chatPanel", { state: "visible" });

  await user2.waitForSelector(".conversation");
  await user2.locator(".conversation", { hasText: "User One" }).first().click();
  await user2.waitForSelector("#chatPanel", { state: "visible" });

  // --- User 1 types and sends a message ---
  await page.click("#messageInput");
  await page.fill("#messageInput", "Hi, this is a real message from user one");
  await page.click('#messageForm button[type="submit"]');

  // User 2 receives it live.
  await user2.waitForFunction(
    () =>
      Array.from(document.querySelectorAll(".bubble")).some((b) =>
        b.textContent.includes("real message from user one")
      ),
    null,
    { timeout: 8000 }
  );
  const u2Saw = await user2
    .locator(".bubble", { hasText: "real message from user one" })
    .count();
  results.push(check(u2Saw > 0, "user 1's typed message reached user 2 live"));

  // The sender's own box cleared after a successful send.
  const cleared = await page.inputValue("#messageInput");
  results.push(check(cleared === "", "composer cleared after send", { cleared }));

  // The no-connection banner stays hidden while the socket is healthy.
  const bannerShown = await page
    .locator("#connBanner")
    .evaluate((el) => getComputedStyle(el).display !== "none");
  results.push(check(!bannerShown, "no connection warning while connected"));

  // --- User 2 replies, having typed it themselves ---
  await user2.click("#messageInput");
  await user2.fill("#messageInput", "Got it - this is user two replying");
  await user2.click('#messageForm button[type="submit"]');

  await page.waitForFunction(
    () =>
      Array.from(document.querySelectorAll(".bubble")).some((b) =>
        b.textContent.includes("user two replying")
      ),
    null,
    { timeout: 8000 }
  );
  results.push(check(true, "user 2's typed reply reached user 1 live"));

  // Each side sees both messages, one mine + one theirs.
  const u1Mine = await page.locator(".msg-row.mine .bubble").count();
  const u1Theirs = await page.locator(".msg-row.theirs .bubble").count();
  const u2Mine = await user2.locator(".msg-row.mine .bubble").count();
  const u2Theirs = await user2.locator(".msg-row.theirs .bubble").count();
  results.push(
    check(
      u1Mine === 1 && u1Theirs === 1 && u2Mine === 1 && u2Theirs === 1,
      "each side shows one sent and one received message",
      { u1Mine, u1Theirs, u2Mine, u2Theirs }
    )
  );

  // Presence: user 1 sees user 2 as online.
  const status = await page.locator("#peerStatus").innerText();
  results.push(check(status === "online", "user 1 sees user 2 online", { status }));

  // NOTE: the "unsent text is not lost" rule can't be tested from inside this
  // script, because it needs the server stopped while the page stays open.
  // test-offline.ps1 does that in two steps.

  await user2.close();
  await ctx2.close();

  return {
    passed: results.filter((r) => r.ok).length,
    failed: results.filter((r) => !r.ok).length,
    results,
  };
}
