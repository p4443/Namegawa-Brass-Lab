const SHEET_NAME = "レッスン予約";
const SLOT_SHEET_NAME = "予約枠状態";
const HEADERS = [
  "受付日時",
  "受付番号",
  "状態",
  "お名前",
  "メールアドレス",
  "電話番号",
  "レッスン種別",
  "希望日",
  "希望時間",
  "ご要望",
];
const SLOT_HEADERS = ["日付", "時間", "状態", "備考", "更新日時", "更新元"];
const SLOT_STATUS_VALUES = ["空き", "調整中", "予約済", "お休み"];
const DUPLICATE_WINDOW_MINUTES = 10;

function doPost(event) {
  const lock = LockService.getScriptLock();
  try {
    const data = JSON.parse(event.postData.contents);
    const secret = PropertiesService.getScriptProperties().getProperty("API_SECRET");
    if (!secret || data.secret !== secret) {
      return jsonResponse({ ok: false, error: "Unauthorized" });
    }

    lock.waitLock(10000);
    const sheet = getReservationSheet();
    const slotSheet = getSlotStatusSheet();
    const action = String(data.action || "create").toLowerCase();
    if (action === "create") {
      const now = new Date();
      const duplicate = findDuplicateReservation(sheet, data, now);
      if (duplicate) {
        return jsonResponse({
          ok: true,
          reservationId: duplicate.reservationId,
          status: duplicate.status || "調整中",
          autoReplySent: false,
          duplicate: true,
        });
      }

      const reservationId = createReservationId(now, sheet.getLastRow());
      sheet.appendRow([
        now,
        reservationId,
        "調整中",
        safeCell(data.name),
        safeCell(data.email),
        safeCell(data.phone),
        safeCell(data.lesson_type),
        safeCell(data.preferred_date),
        safeCell(data.preferred_time),
        safeCell(data.message),
      ]);

      upsertSlotStatus(
        slotSheet,
        String(data.preferred_date || "").trim(),
        String(data.preferred_time || "").trim(),
        "調整中",
        "受付自動設定",
        reservationId,
      );

      const autoReplySent = sendReservationAutoReply(data, reservationId);
      return jsonResponse({
        ok: true,
        reservationId: reservationId,
        status: "調整中",
        autoReplySent: autoReplySent,
        duplicate: false,
      });
    }

    if (action === "get_slot_statuses") {
      const from = String(data.from || "").trim();
      const to = String(data.to || "").trim();
      const slots = listSlotStatuses(slotSheet, from, to);
      return jsonResponse({ ok: true, slots: slots });
    }

    if (action === "upsert_slot_status_range") {
      const startDate = String(data.start_date || "").trim();
      const endDate = String(data.end_date || "").trim();
      const startTime = String(data.start_time || "").trim();
      const endTime = String(data.end_time || "").trim();
      const status = String(data.status || "").trim();
      const note = String(data.note || "").trim();
      if (!startDate || !endDate || !startTime || !endTime || !status) {
        return jsonResponse({ ok: false, error: "Missing range parameters" });
      }
      if (SLOT_STATUS_VALUES.indexOf(status) === -1) {
        return jsonResponse({ ok: false, error: "Invalid slot status" });
      }
      const updatedCount = upsertSlotStatusRange(
        slotSheet,
        startDate,
        endDate,
        startTime,
        endTime,
        status,
        note,
        "admin",
      );
      return jsonResponse({ ok: true, updatedCount: updatedCount });
    }

    if (action === "update") {
      const reservationId = String(data.reservation_id || "").trim();
      if (!reservationId) {
        return jsonResponse({ ok: false, error: "Missing reservation_id" });
      }
      const row = findReservationRowById(sheet, reservationId);
      if (!row) {
        return jsonResponse({ ok: false, error: "NOT_FOUND" });
      }
      const updatedFields = updateReservationRow(sheet, row, data);
      if (updatedFields.length === 0) {
        return jsonResponse({ ok: false, error: "No fields to update" });
      }
      return jsonResponse({
        ok: true,
        reservationId: reservationId,
        updatedFields: updatedFields,
      });
    }

    if (action === "delete") {
      const reservationId = String(data.reservation_id || "").trim();
      if (!reservationId) {
        return jsonResponse({ ok: false, error: "Missing reservation_id" });
      }
      const row = findReservationRowById(sheet, reservationId);
      if (!row) {
        return jsonResponse({ ok: false, error: "NOT_FOUND" });
      }
      sheet.deleteRow(row);
      return jsonResponse({ ok: true, reservationId: reservationId });
    }

    return jsonResponse({ ok: false, error: "Unsupported action" });
  } catch (error) {
    console.error(error);
    return jsonResponse({ ok: false, error: "Failed to save reservation" });
  } finally {
    lock.releaseLock();
  }
}

function getReservationSheet() {
  const spreadsheetId = PropertiesService.getScriptProperties().getProperty("SPREADSHEET_ID");
  const spreadsheet = SpreadsheetApp.openById(spreadsheetId);
  let sheet = spreadsheet.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = spreadsheet.insertSheet(SHEET_NAME);
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, HEADERS.length)
      .setBackground("#0b2545")
      .setFontColor("#ffffff")
      .setFontWeight("bold");
    sheet.getRange("A:A").setNumberFormat("yyyy/mm/dd hh:mm:ss");
    sheet.autoResizeColumns(1, HEADERS.length);
  }
  return sheet;
}

function getSlotStatusSheet() {
  const spreadsheetId = PropertiesService.getScriptProperties().getProperty("SPREADSHEET_ID");
  const spreadsheet = SpreadsheetApp.openById(spreadsheetId);
  let sheet = spreadsheet.getSheetByName(SLOT_SHEET_NAME);
  if (!sheet) {
    sheet = spreadsheet.insertSheet(SLOT_SHEET_NAME);
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(SLOT_HEADERS);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, SLOT_HEADERS.length)
      .setBackground("#1f5f8b")
      .setFontColor("#ffffff")
      .setFontWeight("bold");
    sheet.getRange("A:A").setNumberFormat("yyyy-mm-dd");
    sheet.getRange("E:E").setNumberFormat("yyyy/mm/dd hh:mm:ss");
    sheet.autoResizeColumns(1, SLOT_HEADERS.length);
  }
  return sheet;
}

function createReservationId(date, lastRow) {
  const timeZone = Session.getScriptTimeZone();
  const datePart = Utilities.formatDate(date, timeZone, "yyyyMMdd");
  return `R-${datePart}-${String(lastRow).padStart(3, "0")}`;
}

function findReservationRowById(sheet, reservationId) {
  const lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return 0;
  }
  const values = sheet.getRange(2, 2, lastRow - 1, 1).getValues();
  for (let index = 0; index < values.length; index += 1) {
    if (String(values[index][0]).trim() === reservationId) {
      return index + 2;
    }
  }
  return 0;
}

function updateReservationRow(sheet, row, data) {
  const fieldMap = [
    { key: "status", column: 3 },
    { key: "name", column: 4 },
    { key: "email", column: 5 },
    { key: "phone", column: 6 },
    { key: "lesson_type", column: 7 },
    { key: "preferred_date", column: 8 },
    { key: "preferred_time", column: 9 },
    { key: "message", column: 10 },
  ];
  const updatedFields = [];

  fieldMap.forEach((field) => {
    if (!Object.prototype.hasOwnProperty.call(data, field.key)) {
      return;
    }
    sheet.getRange(row, field.column).setValue(safeCell(data[field.key]));
    updatedFields.push(field.key);
  });

  return updatedFields;
}

function findDuplicateReservation(sheet, data, now) {
  const email = String(data.email || "").trim().toLowerCase();
  const preferredDate = normalizeReservationDate(data.preferred_date);
  const preferredTime = normalizeReservationTime(data.preferred_time);
  if (!email || !preferredDate || !preferredTime) {
    return null;
  }

  const lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return null;
  }

  const values = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
  const duplicateThresholdMs = DUPLICATE_WINDOW_MINUTES * 60 * 1000;
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const row = values[index];
    const createdAt = row[0] instanceof Date ? row[0] : null;
    const reservationId = String(row[1] || "").trim();
    const status = String(row[2] || "").trim();
    const rowEmail = String(row[4] || "").trim().toLowerCase();
    const rowPreferredDate = normalizeReservationDate(row[7]);
    const rowPreferredTime = normalizeReservationTime(row[8]);

    if (status === "キャンセル") {
      continue;
    }
    if (rowEmail !== email || rowPreferredDate !== preferredDate || rowPreferredTime !== preferredTime) {
      continue;
    }
    if (!createdAt || Math.abs(now.getTime() - createdAt.getTime()) > duplicateThresholdMs) {
      continue;
    }

    return {
      reservationId: reservationId,
      status: status,
    };
  }
  return null;
}

function normalizeReservationDate(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd");
  }
  return String(value || "").trim();
}

function normalizeReservationTime(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "HH:mm");
  }
  const text = String(value || "").trim();
  const match = /^(\d{1,2}):([0-5]\d)$/.exec(text);
  if (!match) {
    return text;
  }
  return `${String(Number(match[1])).padStart(2, "0")}:${match[2]}`;
}

function listSlotStatuses(sheet, fromDateText, toDateText) {
  const lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return [];
  }

  const fromDate = parseIsoDate(fromDateText);
  const toDate = parseIsoDate(toDateText);
  const values = sheet.getRange(2, 1, lastRow - 1, SLOT_HEADERS.length).getValues();
  const slots = [];

  values.forEach((row) => {
    const dateText = toDateText_(row[0]);
    if (!dateText) {
      return;
    }
    if (fromDate && dateText < fromDateText) {
      return;
    }
    if (toDate && dateText > toDateText) {
      return;
    }
    slots.push({
      date: dateText,
      time: String(row[1] || "").trim(),
      status: String(row[2] || "").trim(),
      note: String(row[3] || "").trim(),
    });
  });

  return slots;
}

function upsertSlotStatusRange(sheet, startDate, endDate, startTime, endTime, status, note, source) {
  const start = parseIsoDate(startDate);
  const end = parseIsoDate(endDate);
  if (!start || !end || end.getTime() < start.getTime()) {
    throw new Error("Invalid date range");
  }

  let count = 0;
  const oneDay = 24 * 60 * 60 * 1000;
  for (let current = new Date(start.getTime()); current.getTime() <= end.getTime(); current = new Date(current.getTime() + oneDay)) {
    const dateText = Utilities.formatDate(current, Session.getScriptTimeZone(), "yyyy-MM-dd");
    const times = expandTimes(startTime, endTime);
    times.forEach((time) => {
      upsertSlotStatus(sheet, dateText, time, status, note, source);
      count += 1;
    });
  }
  return count;
}

function upsertSlotStatus(sheet, dateText, timeText, status, note, source) {
  if (!dateText || !timeText) {
    return;
  }
  const row = findSlotRow(sheet, dateText, timeText);
  const now = new Date();
  const values = [dateText, timeText, status, safeCell(note), now, safeCell(source || "")];
  if (row > 0) {
    sheet.getRange(row, 1, 1, SLOT_HEADERS.length).setValues([values]);
  } else {
    sheet.appendRow(values);
  }
}

function findSlotRow(sheet, dateText, timeText) {
  const lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return 0;
  }
  const values = sheet.getRange(2, 1, lastRow - 1, 2).getValues();
  for (let index = 0; index < values.length; index += 1) {
    if (toDateText_(values[index][0]) === dateText && String(values[index][1] || "").trim() === timeText) {
      return index + 2;
    }
  }
  return 0;
}

function expandTimes(startTime, endTime) {
  if (startTime === "要相談" && endTime === "要相談") {
    return ["要相談"];
  }
  const start = toMinutes(startTime);
  const end = toMinutes(endTime);
  if (start < 0 || end < 0 || end < start) {
    throw new Error("Invalid time range");
  }
  const times = [];
  for (let minutes = start; minutes <= end; minutes += 15) {
    const hour = Math.floor(minutes / 60);
    const minute = minutes % 60;
    times.push(`${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`);
  }
  return times;
}

function toMinutes(timeText) {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(String(timeText || "").trim());
  if (!match) {
    return -1;
  }
  return Number(match[1]) * 60 + Number(match[2]);
}

function parseIsoDate(dateText) {
  if (!dateText) {
    return null;
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateText);
  if (!match) {
    return null;
  }
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function toDateText_(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd");
  }
  const text = String(value || "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    return text;
  }
  const parsed = parseIsoDate(text);
  if (!parsed) {
    return "";
  }
  return Utilities.formatDate(parsed, Session.getScriptTimeZone(), "yyyy-MM-dd");
}

function sendReservationAutoReply(data, reservationId) {
  const email = String(data.email || "").trim();
  if (!email || email.indexOf("@") <= 0) {
    return false;
  }

  const name = String(data.name || "").trim() || "お客様";
  const lessonType = String(data.lesson_type || "").trim();
  const preferredDate = String(data.preferred_date || "").trim();
  const preferredTime = String(data.preferred_time || "").trim();
  const body = [
    `${name} 様`,
    "",
    "レッスン予約のお申し込みありがとうございます。",
    "以下の内容で確かに受け付けました。",
    "",
    `受付番号: ${reservationId}`,
    `レッスン種別: ${lessonType}`,
    `希望日: ${preferredDate}`,
    `希望時間: ${preferredTime}`,
    "現在の状態: 調整中",
    "",
    "担当より日程確定のご連絡を差し上げます。",
    "このメールは自動送信です。",
  ].join("\n");

  try {
    GmailApp.sendEmail(email, "【なめがわブラス・ラボ】レッスン予約受付完了", body, {
      name: "なめがわブラス・ラボ",
      replyTo: "zuomuj924@gmail.com",
    });
    return true;
  } catch (error) {
    console.error(error);
    return false;
  }
}

function safeCell(value) {
  const text = String(value || "");
  return /^[=+\-@]/.test(text) ? `'${text}` : text;
}

function jsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}