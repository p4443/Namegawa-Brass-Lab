import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../google-apps-script/Code.gs", import.meta.url), "utf8");
const sensitiveError = "Delivery failed: private-student@example.com phone=090-0000-0000";

function harness(attempts = 0) {
  const logs = [];
  const writes = [];
  const emails = [];
  let retries = 0;
  const context = vm.createContext({
    PropertiesService: { getScriptProperties: () => ({ getProperty: () => "test-secret" }) },
    CacheService: { getScriptCache: () => ({ get() {}, put() {} }) },
    LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
    Logger: { log: (value) => logs.push(value) },
    GmailApp: { sendEmail() { throw new Error(sensitiveError); } },
  });
  vm.runInContext(source, context);
  const sheet = {
    getRange(_row, column) {
      return {
        getValue: () => {
          assert.equal(column, 13);
          return attempts;
        },
        setValues: (values) => writes.push(Array.from(values[0])),
      };
    },
  };
  Object.assign(context, {
    getSpreadsheet: () => ({}),
    getReservationSheet: () => sheet,
    findReservationRowById: () => 2,
    getReservationAtRow: () => ({
      name: "Test Student", email: "private-student@example.com", phone: "090-0000-0000",
      lessonType: "体験レッスン", date: "2026-10-31", time: "10:00", status: "確認中",
    }),
    ensureAdminNotificationRetryTrigger: () => { retries += 1; },
    jsonResponse: (value) => value,
  });
  const data = {
    name: "Test Student", email: "private-student@example.com", phone: "090-0000-0000",
    lesson_type: "体験レッスン", preferred_date: "2026-10-31", preferred_time: "10:00",
  };
  return { context, logs, writes, emails, sheet, data, retries: () => retries };
}

test("admin mail failures log a stable code instead of provider error or contact data", () => {
  const h = harness();
  assert.equal(h.context.sendReservationAdminNotification(h.data, "R-test"), false);
  assert.equal(h.context.lastAdminNotificationError, "ADMIN_NOTIFICATION_SEND_FAILED");
  assert.deepEqual(h.logs, ["ADMIN_NOTIFICATION_SEND_FAILED"]);
});

test("failure records preserve retry states and limits without persisting provider text", () => {
  for (const [attempts, status, retries] of [[0, "再送待ち", 1], [4, "要手動再送", 0]]) {
    const h = harness(attempts);
    assert.equal(h.context.sendAndRecordReservationAdminNotification(h.sheet, 2, h.data, "R-test"), false);
    assert.equal(h.writes.length, 2);
    assert.equal(h.writes[0][0], "送信中");
    assert.equal(h.writes[1][0], status);
    assert.equal(h.writes[1][1], attempts + 1);
    assert.equal(h.writes[1][3], "ADMIN_NOTIFICATION_SEND_FAILED");
    assert.equal(h.retries(), retries);
    assert.ok(!JSON.stringify(h.writes).includes("private-student@example.com"));
    assert.ok(!JSON.stringify(h.writes).includes("090-0000-0000"));
  }
});

test("resend API returns its existing failure shape without provider text or contact data", () => {
  const h = harness();
  const result = h.context.doPost({ postData: { contents: JSON.stringify({
    action: "resend_admin_notification", secret: "test-secret", reservation_id: "R-test",
  }) } });
  assert.equal(result.ok, false);
  assert.equal(result.error, "ADMIN_NOTIFICATION_FAILED: ADMIN_NOTIFICATION_SEND_FAILED");
  assert.ok(!JSON.stringify(result).includes("private-student@example.com"));
  assert.ok(!JSON.stringify(result).includes("090-0000-0000"));
});

test("successful admin mail preserves content and clears the previous failure", () => {
  const h = harness();
  h.context.lastAdminNotificationError = "ADMIN_NOTIFICATION_SEND_FAILED";
  h.context.GmailApp.sendEmail = (...args) => h.emails.push(args);
  assert.equal(h.context.sendAndRecordReservationAdminNotification(h.sheet, 2, h.data, "R-test"), true);
  assert.equal(h.context.lastAdminNotificationError, "");
  assert.equal(h.writes[1][0], "送信済");
  assert.equal(h.writes[1][3], "");
  assert.equal(h.retries(), 0);
  assert.equal(h.emails.length, 1);
  assert.equal(h.emails[0][0], h.context.ADMIN_NOTIFICATION_EMAIL);
  assert.match(h.emails[0][2], /private-student@example\.com/);
  assert.match(h.emails[0][2], /090-0000-0000/);
  assert.equal(h.emails[0][3].replyTo, h.data.email);
  assert.deepEqual(h.logs, []);
});
