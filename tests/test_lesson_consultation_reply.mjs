import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../google-apps-script/Code.gs", import.meta.url), "utf8");

function harness(overrides = {}) {
  const emails = [];
  const logs = [];
  const cache = new Map();
  const reservation = {
    name: "相談 太郎", email: "student@example.com", lessonType: "体験レッスン",
    date: "2026-10-31", time: "要相談", status: "確認中", durationMinutes: 30,
    ...overrides,
  };
  const context = vm.createContext({
    PropertiesService: { getScriptProperties: () => ({ getProperty: () => "test-secret" }) },
    CacheService: {
      getScriptCache: () => ({ get: (key) => cache.get(key), put: (key, value) => cache.set(key, value) }),
    },
    LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
    GmailApp: { sendEmail: (...args) => emails.push(args) },
    Logger: { log: (error) => logs.push(error) },
  });
  vm.runInContext(source, context);
  Object.assign(context, {
    getSpreadsheet: () => ({}),
    getReservationSheet: () => ({}),
    getSlotStatusSheet: () => { throw new Error("Must not access slots"); },
    findReservationRowById: (_sheet, id) => id === "R-20261031-001" ? 2 : 0,
    getReservationAtRow: () => reservation,
    jsonResponse: (value) => value,
  });
  function send(values = {}) {
    return context.doPost({ postData: { contents: JSON.stringify({
      secret: "test-secret", action: "send_consultation_reply",
      request_id: "request-1", reservation_id: "R-20261031-001",
      reply_message: "10時はいかがでしょうか。\nご返信ください。",
      ...values,
    }) } });
  }
  return { context, emails, logs, cache, reservation, send };
}

test("sends stored recipient a plain text and escaped UTF-8 HTML answer without changing reservation", () => {
  const h = harness({ name: "<相談>" });
  const before = JSON.stringify(h.reservation);
  const result = h.send({ email: "attacker@example.com", reply_message: "<script>alert(1)</script>\r\n次の行" });
  assert.equal(result.replyEmailSent, true);
  assert.equal(h.emails.length, 1);
  const [to, subject, body, options] = h.emails[0];
  assert.equal(to, "student@example.com");
  assert.match(subject, /ご相談への回答/);
  assert.match(body, /<script>alert\(1\)<\/script>\r\n次の行/);
  assert.match(body, /R-20261031-001/);
  assert.match(options.htmlBody, /charset="UTF-8"/);
  assert.match(options.htmlBody, /&lt;相談&gt;/);
  assert.match(options.htmlBody, /&lt;script&gt;alert\(1\)&lt;\/script&gt;<br>次の行/);
  assert.ok(!options.htmlBody.includes("<script>"));
  assert.equal(options.replyTo, h.context.ADMIN_NOTIFICATION_EMAIL);
  assert.equal(JSON.stringify(h.reservation), before);
});

test("duplicate transport requests reuse cached result and send only once", () => {
  const h = harness();
  assert.equal(h.send().replyEmailSent, true);
  assert.equal(h.send().replyEmailSent, true);
  assert.equal(h.emails.length, 1);
});

test("checks cache again after acquiring lock", () => {
  const h = harness();
  h.context.LockService.getScriptLock = () => ({
    waitLock() {
      h.cache.set("admin:request-1", JSON.stringify({ ok: true, replyEmailSent: true }));
    },
    releaseLock() {},
  });
  assert.equal(h.send().replyEmailSent, true);
  assert.equal(h.emails.length, 0);
});

test("only pending consultation reservations can receive answers", () => {
  for (const overrides of [
    { time: "10:00" }, { status: "確定" }, { status: "キャンセル" }, { status: "" },
  ]) {
    const h = harness(overrides);
    assert.equal(h.send().error, "NOT_PENDING_CONSULTATION");
    assert.equal(h.emails.length, 0);
  }
});

test("rejects missing reservation and invalid or overlong reply without mail", () => {
  for (const reply_message of ["", " \n ", null, 123, "あ".repeat(2001), "🎺".repeat(2001)]) {
    const h = harness();
    assert.equal(h.send({ reply_message }).error, "INVALID_REPLY");
    assert.equal(h.emails.length, 0);
  }
  const h = harness();
  assert.equal(h.send({ reservation_id: "R-20261031-999" }).error, "NOT_FOUND");
  assert.equal(h.emails.length, 0);
});

test("accepts exactly 2000 Unicode code points", () => {
  const h = harness();
  assert.equal(h.send({ reply_message: "🎺".repeat(2000) }).replyEmailSent, true);
  assert.equal(h.emails.length, 1);
});

test("reports invalid stored recipient and Gmail send failures explicitly", () => {
  const invalid = harness({ email: "" });
  assert.equal(invalid.send().error, "REPLY_EMAIL_FAILED");
  assert.equal(invalid.emails.length, 0);
  assert.ok(invalid.logs.length > 0);
  const failed = harness();
  failed.context.GmailApp.sendEmail = () => { throw new Error("quota exceeded"); };
  assert.equal(failed.send().error, "REPLY_EMAIL_FAILED");
  assert.match(String(failed.logs[0]), /quota exceeded/);
  assert.equal(failed.send({ request_id: "retry-2" }).ok, false);
});

test("requires shared secret before sending", () => {
  const h = harness();
  assert.equal(h.send({ secret: "wrong" }).error, "Unauthorized");
  assert.equal(h.emails.length, 0);
});
