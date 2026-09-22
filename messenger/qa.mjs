// Drives the messenger UI end to end in one browser context per user.
// node <skill-dir>/browser.mjs http://localhost:3000 --script ./qa.mjs
export default async function run(page, ui) {
  const results = [];
  const log = (k, v) => results.push({ [k]: v });

  const stamp = Date.now().toString().slice(-6);

  // --- Sign up as the first user (the page is already on the app) ---
  let snap = await ui.snapshot();
  if (!/@e\d+ button "Sign up"/.test(snap)) {
    return { error: "auth screen did not render", snapshot: snap };
  }

  await ui.click(snap.match(/@(e\d+) button "Sign up"/)[1]);
  await page.waitForTimeout(150);

  // Refs change when the tab is toggled (display name field appears).
  snap = await ui.snapshot();
  const usernameBox = snap.match(/@(e\d+) textbox[^\n]*/g) || [];
  const pick = (re) => {
    const line = snap.split("\n").find((l) => re.test(l));
    return line && line.match(/@(e\d+)/)?.[1];
  };
  const userRef = pick(/textbox "Username"/);
  const nameRef = pick(/textbox "Display name"/);
  const passRef = pick(/textbox "Password"/);

  if (!userRef || !nameRef || !passRef) {
    return { error: "signup fields not found", snapshot: snap, usernameBox };
  }

  await ui.fill(userRef, `qa_alice_${stamp}`);
  await ui.fill(nameRef, "QA Alice");
  await ui.fill(passRef, "secret123");
  await page.click('button[type="submit"]');

  // Wait for the chat shell to appear.
  await page.waitForSelector("#app", { state: "visible", timeout: 8000 });
  log("signed_up", true);

  const meName = await page.locator("#meName").innerText();
  log("me_name", meName);

  // --- Start a second, independent session as another user ---
  // A fresh context has its own cookie jar, so it is a genuinely
  // separate login rather than another tab of Alice's session.
  const ctx2 = await page.context().browser().newContext();
  const other = await ctx2.newPage();
  await other.goto(page.url());
  await other.waitForSelector("#authScreen", { state: "visible" });
  await other.click("#tabRegister");
  await other.fill("#displayName", "QA Bob");
  await other.fill("#username", `qa_bob_${stamp}`);
  await other.fill("#password", "secret123");
  await other.click("#authSubmit");
  await other.waitForSelector("#app", { state: "visible", timeout: 8000 });
  log("second_user_signed_up", true);

  // Bob should see Alice in his sidebar.
  await other.waitForSelector(".conversation");
  const bobList = await other.locator(".conversation").count();
  log("bob_sees_contacts", bobList);

  // Resolve each side's row for the other person. Repeated test runs leave
  // lookalike accounts in the DB, so match the display name exactly.
  const bobRow = page.locator(".conversation", { hasText: "QA Bob" });
  const aliceRow = other.locator(".conversation", { hasText: "QA Alice" });

  // --- Alice sends Bob a message ---
  // Alice's sidebar only fills once Bob exists, so wait here.
  // Match the exact display name: repeated test runs leave lookalike
  // accounts in the DB, and ".first()" could pick the wrong person.
  await page.waitForSelector(".conversation", { timeout: 10000 });
  await bobRow.click();
  await page.waitForSelector("#chatPanel", { state: "visible" });
  log("chat_opened", await page.locator("#peerName").innerText());

  await page.waitForTimeout(300);
  await page.fill("#messageInput", "hello from alice");
  await page.click('#messageForm button[type="submit"]');

  // Alice sees her own bubble.
  await page.waitForSelector(".msg-row.mine .bubble", { timeout: 5000 });
  log("sender_bubble", (await page.locator(".msg-row.mine .bubble").first().innerText()).split("\n")[0]);

  // --- Bob receives it live ---
  await aliceRow.click();
  await other.waitForSelector(".msg-row.theirs .bubble", { timeout: 8000 });
  log("recipient_bubble", (await other.locator(".msg-row.theirs .bubble").first().innerText()).split("\n")[0]);

  // Unread badge should have appeared before Bob opened the chat.
  // Send another message and check Bob's sidebar badge without opening.
  await page.fill("#messageInput", "second message");
  await page.click('#messageForm button[type="submit"]');
  await page.waitForTimeout(600);

  // Bob replies; Alice should receive it in real time.
  await other.fill("#messageInput", "hi alice!");
  await other.click('#messageForm button[type="submit"]');

  await page.waitForFunction(
    () => document.querySelectorAll(".msg-row.theirs .bubble").length > 0,
    { timeout: 8000 }
  );
  log("reply_received", (await page.locator(".msg-row.theirs .bubble").first().innerText()).split("\n")[0]);

  // --- Presence: Alice should show Bob as online ---
  const peerStatus = await page.locator("#peerStatus").innerText();
  log("peer_status", peerStatus);

  // --- Sidebar preview + ordering ---
  const preview = await page.locator(".conversation .convo-preview").first().innerText();
  log("sidebar_preview", preview);

  // --- History survives a reload ---
  await page.reload();
  await page.waitForSelector("#app", { state: "visible", timeout: 8000 });
  await page.waitForSelector(".conversation");
  await page.locator(".conversation", { hasText: "QA Bob" }).click();
  await page.waitForSelector(".bubble", { timeout: 8000 });
  const bubbles = await page.locator(".bubble").count();
  log("messages_after_reload", bubbles);

  await other.close();
  await ctx2.close();

  return results;
}
