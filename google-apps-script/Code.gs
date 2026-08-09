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

function safeCell(value) {
  const text = String(value || "");
  return /^[=+\-@]/.test(text) ? `'${text}` : text;
}

function jsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}