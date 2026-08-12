var SHEET_NAME = "レッスン予約";
var SLOT_SHEET_NAME = "予約枠状態";
var HEADERS = [
  "受付日時",
  "受付番号",
  "状態",
  "お名前",
  "メールアドレス",
  "電話番号",
  "レッスン種別",
  "希望日",
  "希望時間",
  "ご要望"
];
var SLOT_HEADERS = ["日付", "時間", "状態", "備考", "更新日時", "更新元"];
var SLOT_STATUS_VALUES = ["空き", "調整中", "予約済", "お休み"];
var DUPLICATE_WINDOW_MINUTES = 10;
var SCRIPT_VERSION = "2026-08-12-admin-v4";
var LESSON_DURATION_MINUTES = {
  "体験レッスン": 30,
  "無料体験レッスン": 30,
  "小学生": 30,
  "中学生": 45,
  "高校生以上": 60,
  "グループ・部活動指導": 60
};

function doPost(event) {
  var lock = LockService.getScriptLock();
  var lockAcquired = false;
  try {
    if (!event || !event.postData || !event.postData.contents) {
      return jsonResponse({
        ok: false,
        error: "doPostはウェブアプリへのPOSTで実行してください。エディターの実行ボタンでは動作確認できません。"
      });
    }
    var data = JSON.parse(event.postData.contents);
    var secret = PropertiesService.getScriptProperties().getProperty("API_SECRET");
    if (!secret || data.secret !== secret) {
      return jsonResponse({ ok: false, error: "Unauthorized" });
    }

    var action = String(data.action || "create").toLowerCase();
    if (action === "health") {
      return jsonResponse({
        ok: true,
        version: SCRIPT_VERSION,
        capabilities: ["list", "update", "delete", "upsert_slot_status_range"]
      });
    }

    var requestId = String(data.request_id || "").trim();
    if ((action === "update" || action === "delete") && requestId) {
      var cachedResult = CacheService.getScriptCache().get("admin:" + requestId);
      if (cachedResult) {
        return jsonResponse(JSON.parse(cachedResult));
      }
    }

    var writeActions = ["create", "upsert_slot_status_range", "update", "delete"];
    if (writeActions.indexOf(action) !== -1) {
      lock.waitLock(10000);
      lockAcquired = true;
    }
    var spreadsheet = getSpreadsheet();
    var needsReservationSheet = ["create", "list", "update", "delete"].indexOf(action) !== -1;
    var needsSlotSheet = ["create", "get_slot_statuses", "upsert_slot_status_range", "update", "delete"].indexOf(action) !== -1;
    var sheet = needsReservationSheet ? getReservationSheet(spreadsheet) : null;
    var slotSheet = needsSlotSheet ? getSlotStatusSheet(spreadsheet) : null;
    if (action === "create") {
      var now = new Date();
      var duplicate = findDuplicateReservation(sheet, data, now);
      if (duplicate) {
        return jsonResponse({
          ok: true,
          reservationId: duplicate.reservationId,
          status: duplicate.status || "調整中",
          autoReplySent: false,
          duplicate: true
        });
      }

      var preferredDate = String(data.preferred_date || "").trim();
      var preferredTime = String(data.preferred_time || "").trim();
      var durationMinutes = getLessonDuration(data.lesson_type);
      var occupiedTimes = reservationSlotTimes(preferredTime, durationMinutes);
      var slotConflict = findReservationSlotConflict(slotSheet, preferredDate, occupiedTimes, "");
      if (slotConflict) {
        return jsonResponse({
          ok: true,
          reservationId: "",
          status: slotConflict.status,
          autoReplySent: false,
          duplicate: false,
          conflict: true
        });
      }

      var reservationId = createReservationId(now, sheet.getLastRow());
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
        safeCell(data.message)
      ]);

      occupiedTimes.forEach(function (time) {
        upsertSlotStatus(
          slotSheet,
          preferredDate,
          time,
          "調整中",
          durationMinutes + "分レッスン受付",
          reservationId
        );
      });

      var autoReplySent = sendReservationAutoReply(data, reservationId);
      return jsonResponse({
        ok: true,
        reservationId: reservationId,
        status: "調整中",
        autoReplySent: autoReplySent,
        duplicate: false
      });
    }

    if (action === "get_slot_statuses") {
      var from = String(data.from || "").trim();
      var to = String(data.to || "").trim();
      var slots = listSlotStatuses(slotSheet, from, to);
      return jsonResponse({ ok: true, slots: slots });
    }

    if (action === "list") {
      return jsonResponse({ ok: true, reservations: listReservations(sheet) });
    }

    if (action === "upsert_slot_status_range") {
      var startDate = String(data.start_date || "").trim();
      var endDate = String(data.end_date || "").trim();
      var startTime = String(data.start_time || "").trim();
      var endTime = String(data.end_time || "").trim();
      var status = String(data.status || "").trim();
      var note = String(data.note || "").trim();
      if (!startDate || !endDate || !startTime || !endTime || !status) {
        return jsonResponse({ ok: false, error: "Missing range parameters" });
      }
      if (SLOT_STATUS_VALUES.indexOf(status) === -1) {
        return jsonResponse({ ok: false, error: "Invalid slot status" });
      }
      var updatedCount = upsertSlotStatusRange(
        slotSheet,
        startDate,
        endDate,
        startTime,
        endTime,
        status,
        note,
        "admin"
      );
      return jsonResponse({ ok: true, updatedCount: updatedCount });
    }

    if (action === "update") {
      var reservationId = String(data.reservation_id || "").trim();
      if (!reservationId) {
        return adminActionResponse({ ok: false, error: "Missing reservation_id" }, requestId);
      }
      var row = findReservationRowById(sheet, reservationId);
      if (!row) {
        return adminActionResponse({ ok: false, error: "NOT_FOUND" }, requestId);
      }
      var currentReservation = getReservationAtRow(sheet, row);
      var nextDate = Object.prototype.hasOwnProperty.call(data, "preferred_date")
        ? String(data.preferred_date || "").trim()
        : currentReservation.date;
      var nextTime = Object.prototype.hasOwnProperty.call(data, "preferred_time")
        ? String(data.preferred_time || "").trim()
        : currentReservation.time;
      var nextStatus = Object.prototype.hasOwnProperty.call(data, "status")
        ? String(data.status || "").trim()
        : currentReservation.status;
      var nextLessonType = Object.prototype.hasOwnProperty.call(data, "lesson_type")
        ? String(data.lesson_type || "").trim()
        : currentReservation.lessonType;
      var nextSlotStatus = reservationStatusToSlotStatus(nextStatus);
      var currentTimes = reservationSlotTimes(
        currentReservation.time,
        getLessonDuration(currentReservation.lessonType)
      );
      var nextTimes = reservationSlotTimes(nextTime, getLessonDuration(nextLessonType));
      var slotConflict = nextSlotStatus === "空き"
        ? null
        : findReservationSlotConflict(slotSheet, nextDate, nextTimes, reservationId);
      if (slotConflict) {
        return adminActionResponse({
          ok: true,
          reservationId: reservationId,
          status: slotConflict.status,
          conflict: true
        }, requestId);
      }
      var updatedFields = updateReservationRow(sheet, row, data);
      if (updatedFields.length === 0) {
        return adminActionResponse({ ok: false, error: "No fields to update" }, requestId);
      }
      releaseReservationSlots(slotSheet, currentReservation.date, currentTimes, reservationId);
      if (nextSlotStatus !== "空き") {
        nextTimes.forEach(function (time) {
          upsertSlotStatus(
            slotSheet,
            nextDate,
            time,
            nextSlotStatus,
            getLessonDuration(nextLessonType) + "分レッスン更新",
            reservationId
          );
        });
      }
      return adminActionResponse({
        ok: true,
        reservationId: reservationId,
        status: nextStatus,
        updatedFields: updatedFields
      }, requestId);
    }

    if (action === "delete") {
      var reservationId = String(data.reservation_id || "").trim();
      if (!reservationId) {
        return adminActionResponse({ ok: false, error: "Missing reservation_id" }, requestId);
      }
      var row = findReservationRowById(sheet, reservationId);
      if (!row) {
        return adminActionResponse({ ok: false, error: "NOT_FOUND" }, requestId);
      }
      var reservation = getReservationAtRow(sheet, row);
      releaseReservationSlots(
        slotSheet,
        reservation.date,
        reservationSlotTimes(reservation.time, getLessonDuration(reservation.lessonType)),
        reservationId
      );
      sheet.deleteRow(row);
      return adminActionResponse({ ok: true, reservationId: reservationId }, requestId);
    }

    return jsonResponse({ ok: false, error: "Unsupported action" });
  } catch (error) {
    Logger.log(error);
    return jsonResponse({
      ok: false,
      error: "Failed to save reservation",
      detail: String(error && error.message ? error.message : error)
    });
  } finally {
    if (lockAcquired) {
      lock.releaseLock();
    }
  }
}

function getSpreadsheet() {
  var spreadsheetId = PropertiesService.getScriptProperties().getProperty("SPREADSHEET_ID");
  return SpreadsheetApp.openById(spreadsheetId);
}

function getReservationSheet(spreadsheet) {
  var sheet = spreadsheet.getSheetByName(SHEET_NAME);
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

function getSlotStatusSheet(spreadsheet) {
  var sheet = spreadsheet.getSheetByName(SLOT_SHEET_NAME);
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
  var timeZone = Session.getScriptTimeZone();
  var datePart = Utilities.formatDate(date, timeZone, "yyyyMMdd");
  return "R-" + datePart + "-" + zeroPad(lastRow, 3);
}

function findReservationRowById(sheet, reservationId) {
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return 0;
  }
  var values = sheet.getRange(2, 2, lastRow - 1, 1).getValues();
  for (var index = 0; index < values.length; index += 1) {
    if (String(values[index][0]).trim() === reservationId) {
      return index + 2;
    }
  }
  return 0;
}

function updateReservationRow(sheet, row, data) {
  var fieldMap = [
    { key: "status", column: 3 },
    { key: "name", column: 4 },
    { key: "email", column: 5 },
    { key: "phone", column: 6 },
    { key: "lesson_type", column: 7 },
    { key: "preferred_date", column: 8 },
    { key: "preferred_time", column: 9 },
    { key: "message", column: 10 }
  ];
  var updatedFields = [];

  fieldMap.forEach(function (field) {
    if (!Object.prototype.hasOwnProperty.call(data, field.key)) {
      return;
    }
    sheet.getRange(row, field.column).setValue(safeCell(data[field.key]));
    updatedFields.push(field.key);
  });

  return updatedFields;
}

function getReservationAtRow(sheet, row) {
  var values = sheet.getRange(row, 3, 1, 7).getValues()[0];
  return {
    status: String(values[0] || "").trim(),
    lessonType: String(values[4] || "").trim(),
    date: normalizeReservationDate(values[5]),
    time: normalizeReservationTime(values[6])
  };
}

function listReservations(sheet) {
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return [];
  }
  var values = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
  return values.map(function (row) {
    return {
      received_at: row[0] instanceof Date
        ? Utilities.formatDate(row[0], Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm")
        : String(row[0] || "").trim(),
      reservation_id: String(row[1] || "").trim(),
      status: String(row[2] || "").trim(),
      name: String(row[3] || "").trim(),
      email: String(row[4] || "").trim(),
      phone: String(row[5] || "").trim(),
      lesson_type: String(row[6] || "").trim(),
      duration_minutes: getLessonDuration(row[6]),
      preferred_date: normalizeReservationDate(row[7]),
      preferred_time: normalizeReservationTime(row[8]),
      message: String(row[9] || "").trim()
    };
  }).sort(function (left, right) {
    var dateOrder = right.preferred_date.localeCompare(left.preferred_date);
    return dateOrder || right.preferred_time.localeCompare(left.preferred_time);
  });
}

function reservationStatusToSlotStatus(status) {
  if (status === "キャンセル") {
    return "空き";
  }
  if (status === "確定") {
    return "予約済";
  }
  return "調整中";
}

function findDuplicateReservation(sheet, data, now) {
  var email = String(data.email || "").trim().toLowerCase();
  var preferredDate = normalizeReservationDate(data.preferred_date);
  var preferredTime = normalizeReservationTime(data.preferred_time);
  if (!email || !preferredDate || !preferredTime) {
    return null;
  }

  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return null;
  }

  var values = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
  for (var index = values.length - 1; index >= 0; index -= 1) {
    var row = values[index];
    var receivedAt = row[0] instanceof Date ? row[0] : new Date(row[0]);
    var reservationId = String(row[1] || "").trim();
    var status = String(row[2] || "").trim();
    var rowEmail = String(row[4] || "").trim().toLowerCase();
    var rowPreferredDate = normalizeReservationDate(row[7]);
    var rowPreferredTime = normalizeReservationTime(row[8]);

    if (status === "キャンセル") {
      continue;
    }
    if (
      isNaN(receivedAt.getTime())
      || now.getTime() - receivedAt.getTime() > DUPLICATE_WINDOW_MINUTES * 60 * 1000
    ) {
      continue;
    }
    if (rowEmail !== email || rowPreferredDate !== preferredDate || rowPreferredTime !== preferredTime) {
      continue;
    }

    return {
      reservationId: reservationId,
      status: status
    };
  }
  return null;
}

function normalizeReservationDate(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd");
  }
  var text = String(value || "").trim();
  if (!text) {
    return "";
  }
  var directMatch = /^(\d{4})[-\/](\d{1,2})[-\/](\d{1,2})$/.exec(text);
  if (directMatch) {
    return directMatch[1] + "-" + zeroPad(Number(directMatch[2]), 2) + "-" + zeroPad(Number(directMatch[3]), 2);
  }
  var parsed = new Date(text);
  if (!isNaN(parsed.getTime())) {
    return Utilities.formatDate(parsed, Session.getScriptTimeZone(), "yyyy-MM-dd");
  }
  return text;
}

function normalizeReservationTime(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "HH:mm");
  }
  var text = String(value || "").trim();
  var match = /^(\d{1,2}):([0-5]\d)(?::([0-5]\d))?$/.exec(text);
  if (!match) {
    var parsed = new Date(text);
    if (!isNaN(parsed.getTime())) {
      return Utilities.formatDate(parsed, Session.getScriptTimeZone(), "HH:mm");
    }
    return text;
  }
  return zeroPad(Number(match[1]), 2) + ":" + match[2];
}

function listSlotStatuses(sheet, fromDateText, toDateText) {
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return [];
  }

  var fromDate = parseIsoDate(fromDateText);
  var toDate = parseIsoDate(toDateText);
  var values = sheet.getRange(2, 1, lastRow - 1, SLOT_HEADERS.length).getValues();
  var slots = [];

  values.forEach(function (row) {
    var dateText = toDateText_(row[0]);
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
      time: normalizeReservationTime(row[1]),
      status: String(row[2] || "").trim(),
      note: String(row[3] || "").trim()
    });
  });

  return slots;
}

function upsertSlotStatusRange(sheet, startDate, endDate, startTime, endTime, status, note, source) {
  var start = parseIsoDate(startDate);
  var end = parseIsoDate(endDate);
  if (!start || !end || end.getTime() < start.getTime()) {
    throw new Error("Invalid date range");
  }

  var lastRow = sheet.getLastRow();
  var rows = lastRow > 1
    ? sheet.getRange(2, 1, lastRow - 1, SLOT_HEADERS.length).getValues()
    : [];
  var rowIndexes = {};
  rows.forEach(function (row, index) {
    var dateText = toDateText_(row[0]);
    var timeText = normalizeReservationTime(row[1]);
    if (dateText && timeText) {
      rowIndexes[dateText + "|" + timeText] = index;
    }
  });

  var count = 0;
  var oneDay = 24 * 60 * 60 * 1000;
  for (var current = new Date(start.getTime()); current.getTime() <= end.getTime(); current = new Date(current.getTime() + oneDay)) {
    var dateText = Utilities.formatDate(current, Session.getScriptTimeZone(), "yyyy-MM-dd");
    var times = expandTimes(startTime, endTime);
    times.forEach(function (time) {
      var values = [dateText, time, status, safeCell(note), new Date(), safeCell(source || "")];
      var key = dateText + "|" + time;
      if (Object.prototype.hasOwnProperty.call(rowIndexes, key)) {
        rows[rowIndexes[key]] = values;
      } else {
        rowIndexes[key] = rows.length;
        rows.push(values);
      }
      count += 1;
    });
  }
  if (rows.length > 0) {
    sheet.getRange(2, 1, rows.length, SLOT_HEADERS.length).setValues(rows);
  }
  return count;
}

function upsertSlotStatus(sheet, dateText, timeText, status, note, source) {
  if (!dateText || !timeText) {
    return;
  }
  var row = findSlotRow(sheet, dateText, timeText);
  var now = new Date();
  var values = [dateText, timeText, status, safeCell(note), now, safeCell(source || "")];
  if (row > 0) {
    sheet.getRange(row, 1, 1, SLOT_HEADERS.length).setValues([values]);
  } else {
    sheet.appendRow(values);
  }
}

function findSlotRow(sheet, dateText, timeText) {
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return 0;
  }
  var values = sheet.getRange(2, 1, lastRow - 1, 2).getValues();
  for (var index = 0; index < values.length; index += 1) {
    if (toDateText_(values[index][0]) === dateText && normalizeReservationTime(values[index][1]) === timeText) {
      return index + 2;
    }
  }
  return 0;
}

function getSlotStatus(sheet, dateText, timeText) {
  return getSlotRecord(sheet, dateText, timeText).status;
}

function getLessonDuration(lessonType) {
  return LESSON_DURATION_MINUTES[String(lessonType || "").trim()] || 60;
}

function reservationSlotTimes(startTime, durationMinutes) {
  if (startTime === "要相談") {
    return ["要相談"];
  }
  var start = toMinutes(startTime);
  if (start < 0 || durationMinutes <= 0 || durationMinutes % 15 !== 0) {
    throw new Error("Invalid reservation duration");
  }
  var times = [];
  for (var offset = 0; offset < durationMinutes; offset += 15) {
    var minutes = start + offset;
    times.push(zeroPad(Math.floor(minutes / 60), 2) + ":" + zeroPad(minutes % 60, 2));
  }
  return times;
}

function findReservationSlotConflict(sheet, dateText, times, reservationId) {
  for (var index = 0; index < times.length; index += 1) {
    var slot = getSlotRecord(sheet, dateText, times[index]);
    if (slot.status && slot.status !== "空き" && slot.source !== reservationId) {
      return slot;
    }
  }
  return null;
}

function getSlotRecord(sheet, dateText, timeText) {
  var row = findSlotRow(sheet, dateText, timeText);
  if (!row) {
    return { status: "", source: "" };
  }
  var values = sheet.getRange(row, 3, 1, 4).getValues()[0];
  return {
    status: String(values[0] || "").trim(),
    source: String(values[3] || "").trim()
  };
}

function releaseReservationSlots(sheet, dateText, times, reservationId) {
  var releasedCount = 0;
  times.forEach(function (time) {
    var slot = getSlotRecord(sheet, dateText, time);
    if (slot.source !== reservationId) {
      return;
    }
    upsertSlotStatus(sheet, dateText, time, "空き", "予約枠自動解放", reservationId);
    releasedCount += 1;
  });
  return releasedCount;
}

function expandTimes(startTime, endTime) {
  if (startTime === "要相談" && endTime === "要相談") {
    return ["要相談"];
  }
  var start = toMinutes(startTime);
  var end = toMinutes(endTime);
  if (start < 0 || end < 0 || end < start) {
    throw new Error("Invalid time range");
  }
  var times = [];
  for (var minutes = start; minutes <= end; minutes += 15) {
    var hour = Math.floor(minutes / 60);
    var minute = minutes % 60;
    times.push(zeroPad(hour, 2) + ":" + zeroPad(minute, 2));
  }
  return times;
}

function toMinutes(timeText) {
  var match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(String(timeText || "").trim());
  if (!match) {
    return -1;
  }
  return Number(match[1]) * 60 + Number(match[2]);
}

function parseIsoDate(dateText) {
  if (!dateText) {
    return null;
  }
  var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateText);
  if (!match) {
    return null;
  }
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function toDateText_(value) {
  if (value instanceof Date) {
    return Utilities.formatDate(value, Session.getScriptTimeZone(), "yyyy-MM-dd");
  }
  var text = String(value || "").trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    return text;
  }
  var parsed = parseIsoDate(text);
  if (!parsed) {
    return "";
  }
  return Utilities.formatDate(parsed, Session.getScriptTimeZone(), "yyyy-MM-dd");
}

function sendReservationAutoReply(data, reservationId) {
  var email = String(data.email || "").trim();
  if (!email || email.indexOf("@") <= 0) {
    return false;
  }

  var name = String(data.name || "").trim() || "お客様";
  var lessonType = String(data.lesson_type || "").trim();
  var preferredDate = String(data.preferred_date || "").trim();
  var preferredTime = String(data.preferred_time || "").trim();
  var body = [
    name + " 様",
    "",
    "レッスン予約のお申し込みありがとうございます。",
    "以下の内容で確かに受け付けました。",
    "",
    "受付番号: " + reservationId,
    "レッスン種別: " + lessonType,
    "所要時間: " + getLessonDuration(lessonType) + "分",
    "希望日: " + preferredDate,
    "希望時間: " + preferredTime,
    "現在の状態: 調整中",
    "",
    "担当より日程確定のご連絡を差し上げます。",
    "このメールは自動送信です。"
  ].join("\n");

  try {
    GmailApp.sendEmail(email, "【なめがわブラス・ラボ】レッスン予約受付完了", body, {
      name: "なめがわブラス・ラボ",
      replyTo: "zuomuj924@gmail.com"
    });
    return true;
  } catch (error) {
    Logger.log(error);
    return false;
  }
}

function safeCell(value) {
  var text = String(value || "");
  return /^[=+\-@]/.test(text) ? "'" + text : text;
}

function zeroPad(value, width) {
  var text = String(value);
  while (text.length < width) {
    text = "0" + text;
  }
  return text;
}

function jsonResponse(data) {
  return ContentService.createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

function adminActionResponse(data, requestId) {
  if (requestId) {
    CacheService.getScriptCache().put("admin:" + requestId, JSON.stringify(data), 600);
  }
  return jsonResponse(data);
}