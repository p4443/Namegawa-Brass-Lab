import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../google-apps-script/Code.gs", import.meta.url), "utf8");

function harness(rows = [], now = "2026-10-30T15:00:00Z") {
  const events = [];
  const logs = [];
  const triggers = [];
  const created = [];
  class Clock extends Date {
    constructor(...args) {
      super(...(args.length ? args : [now]));
    }
  }
  const context = vm.createContext({
    Date: Clock,
    Logger: { log: (message) => logs.push(message) },
    Utilities: {
      formatDate(value, timezone, pattern) {
        assert.equal(timezone, "Asia/Tokyo");
        assert.equal(pattern, "yyyy-MM-dd");
        const parts = new Intl.DateTimeFormat("en-US", {
          timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
        }).formatToParts(value);
        const part = (type) => parts.find((entry) => entry.type === type).value;
        return `${part("year")}-${part("month")}-${part("day")}`;
      },
    },
    LockService: {
      getScriptLock: () => ({
        waitLock(timeout) { events.push(["lock", timeout]); },
        releaseLock() { events.push(["unlock"]); },
      }),
    },
    ScriptApp: {
      getProjectTriggers: () => triggers,
      newTrigger(handler) {
        const settings = { handler };
        const builder = {
          timeBased() { settings.type = "clock"; return builder; },
          atHour(hour) { settings.hour = hour; return builder; },
          everyDays(days) { settings.days = days; return builder; },
          inTimezone(timezone) { settings.timezone = timezone; return builder; },
          create() {
            created.push(settings);
            triggers.push({ getHandlerFunction: () => handler });
          },
        };
        return builder;
      },
    },
  });
  vm.runInContext(source, context);
  const sheet = {
    getLastRow: () => rows.length + 1,
    getRange(row, column, count, width) {
      events.push(["read", row, column, count, width]);
      if (row === 1) return { getValues: () => [context.HEADERS.slice(0, 11)] };
      assert.equal(width, 1, "Must not read names or contact data");
      assert.ok(column === 3 || column === 8);
      return {
        getValues: () => rows.slice(row - 2, row - 2 + count).map(
          (entry) => [column === 3 ? entry.status : entry.date],
        ),
      };
    },
    deleteRow(row) {
      events.push(["delete", row]);
      rows.splice(row - 2, 1);
    },
  };
  context.getSpreadsheet = () => ({
    getSheetByName(name) {
      assert.equal(name, "レッスン予約");
      return sheet;
    },
  });
  return { context, rows, sheet, events, logs, triggers, created };
}

test("deletes only prior-day confirmed rows from bottom to top without other side effects", () => {
  const rows = [
    { status: "確定", date: "2026-10-29" },
    { status: "確定", date: "2026-10-30" },
    { status: "確定", date: "2026-10-31" },
    { status: "確定", date: "2026-11-01" },
    { status: "確認中", date: "2026-10-01" },
    { status: "キャンセル", date: "2026-10-01" },
    { status: "調整中", date: "2026-10-01" },
    { status: "", date: "2026-10-01" },
    { status: " 確定 ", date: "2026/10/1" },
  ];
  const h = harness(rows);
  assert.equal(h.context.deleteExpiredConfirmedReservations(), 3);
  assert.deepEqual(h.events.filter((entry) => entry[0] === "delete"),
    [["delete", 10], ["delete", 3], ["delete", 2]]);
  assert.equal(rows.length, 6);
  assert.equal(rows[0].date, "2026-10-31");
  assert.deepEqual(h.events[0], ["lock", 10000]);
  assert.deepEqual(h.events.at(-1), ["unlock"]);
  assert.deepEqual(h.logs, ["Expired confirmed reservations deleted: 3; invalid dates retained: 0"]);
});

test("uses Japan midnight boundary rather than UTC date or lesson end time", () => {
  const before = harness([{ status: "確定", date: "2026-10-30" }], "2026-10-30T14:59:59Z");
  assert.equal(before.context.deleteExpiredConfirmedReservations(), 0);
  const after = harness([{ status: "確定", date: "2026-10-30" }], "2026-10-30T15:00:00Z");
  assert.equal(after.context.deleteExpiredConfirmedReservations(), 1);
});

test("handles Japan year boundary, leap days, and spreadsheet Date cells", () => {
  const h = harness([
    { status: "確定", date: "2026-12-31" },
    { status: "確定", date: "2027-01-01" },
    { status: "確定", date: "2024-02-29" },
  ], "2026-12-31T15:00:00Z");
  h.rows.push({ status: "確定", date: new h.context.Date("2026-12-30T15:00:00Z") });
  h.rows.push({ status: "確定", date: new h.context.Date("2026-12-31T15:00:00Z") });
  assert.equal(h.context.deleteExpiredConfirmedReservations(), 3);
  assert.equal(h.rows.length, 2);
});

test("retains malformed dates and reports only their count", () => {
  const dates = ["", "not-a-date", "2026-02-30", "2026-00-01", "2026-13-01",
    "2026-01-00", "2025-02-29", "2026-10-01T10:00:00Z", null, 123, "10/1/2026"];
  const h = harness(dates.map((date) => ({ status: "確定", date })));
  h.rows.push({ status: "確定", date: new h.context.Date(NaN) });
  assert.equal(h.context.deleteExpiredConfirmedReservations(), 0);
  assert.equal(h.rows.length, 12);
  assert.deepEqual(h.logs, ["Expired confirmed reservations deleted: 0; invalid dates retained: 12"]);
});

test("empty sheet and repeated cleanup are safe", () => {
  const empty = harness();
  assert.equal(empty.context.deleteExpiredConfirmedReservations(), 0);
  assert.equal(empty.events.filter((event) => event[0] === "read").length, 1);
  const h = harness([{ status: "確定", date: "2026-10-01" }]);
  assert.equal(h.context.deleteExpiredConfirmedReservations(), 1);
  assert.equal(h.context.deleteExpiredConfirmedReservations(), 0);
});

test("missing sheet or mismatched headers abort before deletion and release lock", () => {
  const missing = harness();
  missing.context.getSpreadsheet = () => ({ getSheetByName: () => null });
  assert.throws(() => missing.context.deleteExpiredConfirmedReservations(), /sheet is missing/);
  assert.deepEqual(missing.events.at(-1), ["unlock"]);
  const h = harness([{ status: "確定", date: "2026-10-01" }]);
  h.sheet.getRange = () => ({ getValues: () => [["wrong headers"]] });
  assert.throws(() => h.context.deleteExpiredConfirmedReservations(), /headers do not match/);
  assert.equal(h.rows.length, 1);
  assert.deepEqual(h.events.at(-1), ["unlock"]);
});

test("spreadsheet failures propagate and do not produce success-shaped logs", () => {
  const h = harness([{ status: "確定", date: "2026-10-01" }]);
  h.sheet.deleteRow = () => { throw new Error("quota exceeded"); };
  assert.throws(() => h.context.deleteExpiredConfirmedReservations(), /quota exceeded/);
  assert.deepEqual(h.events.at(-1), ["unlock"]);
  assert.deepEqual(h.logs, []);
});

test("lock contention is explicit and never deletes without the lock", () => {
  const h = harness([{ status: "確定", date: "2026-10-01" }]);
  h.context.LockService.getScriptLock = () => ({
    waitLock() { throw new Error("lock timeout"); },
    releaseLock() { throw new Error("Must not release unacquired lock"); },
  });
  assert.throws(() => h.context.deleteExpiredConfirmedReservations(), /lock timeout/);
  assert.equal(h.rows.length, 1);
});

test("installs a single daily Japan-time trigger without touching reservations or other triggers", () => {
  const h = harness([{ status: "確定", date: "2026-10-01" }]);
  h.triggers.push({ getHandlerFunction: () => "retryPendingAdminNotifications" });
  h.context.installExpiredConfirmedReservationCleanup();
  h.context.installExpiredConfirmedReservationCleanup();
  assert.deepEqual(h.created, [{
    handler: "deleteExpiredConfirmedReservations", type: "clock",
    hour: 1, days: 1, timezone: "Asia/Tokyo",
  }]);
  assert.equal(h.triggers.length, 2);
  assert.equal(h.rows.length, 1);
  assert.equal(h.events.filter((event) => event[0] === "read" || event[0] === "delete").length, 0);
  assert.equal(h.events.filter((event) => event[0] === "unlock").length, 2);
});

test("trigger creation errors propagate and release lock", () => {
  const h = harness();
  h.context.ScriptApp.newTrigger = () => { throw new Error("permission denied"); };
  assert.throws(() => h.context.installExpiredConfirmedReservationCleanup(), /permission denied/);
  assert.deepEqual(h.events.at(-1), ["unlock"]);
  assert.deepEqual(h.logs, []);
});
