const SHEET_NAME = "レッスン予約";
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
    const action = String(data.action || "create").toLowerCase();
    if (action === "create") {
      const now = new Date();
      const reservationId = createReservationId(now, sheet.getLastRow());
      sheet.appendRow([
        now,
        reservationId,
        "受付",
        safeCell(data.name),
        safeCell(data.email),
        safeCell(data.phone),
        safeCell(data.lesson_type),
        safeCell(data.preferred_date),
        safeCell(data.preferred_time),
        safeCell(data.message),
      ]);
      return jsonResponse({ ok: true, reservationId: reservationId });
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

function safeCell(value) {
  const text = String(value || "");
  return /^[=+\-@]/.test(text) ? `'${text}` : text;
}

function jsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}