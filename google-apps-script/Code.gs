var SHEET_NAME = "レッスン予約";
var SLOT_SHEET_NAME = "予約枠状態";
var CONSULTATION_SHEET_NAME = "企画・輸送相談";
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
  "ご要望",
  "所要時間（分）"
];
var SLOT_HEADERS = ["日付", "時間", "状態", "備考", "更新日時", "更新元"];
var CONSULTATION_HEADERS = [
  "受付日時",
  "受付番号",
  "サービス種別",
  "団体名・お申込者名",
  "メールアドレス",
  "実施予定日",
  "サポート内容",
  "楽器総評価額",
  "企画種別",
  "搬送概要",
  "相談詳細",
  "添付ファイル名",
  "添付ファイルURL"
];
var SLOT_STATUS_VALUES = ["空き", "調整中", "予約済", "お休み"];
var DUPLICATE_WINDOW_MINUTES = 10;
var MAX_ACTIVE_RESERVATIONS_PER_EMAIL = 4;
var ADMIN_NOTIFICATION_EMAIL = "zuomuj924@gmail.com";
var SCRIPT_VERSION = "2026-08-31-google-routes-required-v30";
var LESSON_DURATION_MINUTES = {
  "体験レッスン": 30,
  "無料体験レッスン": 30,
  "小学生": 30,
  "中学生": 45,
  "高校生以上": 60,
  "高校生以上・大人": 60,
  "グループ・部活動指導": 0
};

function doPost(event) {
  var lock = null;
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
    lock = action === "generate_transport_sheet"
      ? LockService.getUserLock()
      : LockService.getScriptLock();
    if (action === "health") {
      return jsonResponse({
        ok: true,
        version: SCRIPT_VERSION,
        capabilities: ["consultation", "generate_transport_sheet", "list", "update", "delete", "cancel", "upsert_slot_status_range"]
      });
    }

    var requestId = String(data.request_id || "").trim();
    if ((action === "consultation" || action === "generate_transport_sheet" || action === "update" || action === "delete" || action === "cancel") && requestId) {
      var cachedResult = CacheService.getScriptCache().get("admin:" + requestId);
      if (cachedResult) {
        return jsonResponse(JSON.parse(cachedResult));
      }
    }

    var writeActions = ["create", "consultation", "generate_transport_sheet", "upsert_slot_status_range", "update", "delete", "cancel"];
    if (writeActions.indexOf(action) !== -1) {
      lock.waitLock(10000);
      lockAcquired = true;
      if (requestId) {
        var lockedCachedResult = CacheService.getScriptCache().get("admin:" + requestId);
        if (lockedCachedResult) {
          return jsonResponse(JSON.parse(lockedCachedResult));
        }
      }
    }
    if (action === "generate_transport_sheet") {
      return adminActionResponse(generateTransportWorkbook(data), requestId);
    }
    var spreadsheet = getSpreadsheet();
    var needsReservationSheet = ["create", "list", "get_slot_statuses", "update", "delete", "cancel"].indexOf(action) !== -1;
    var needsSlotSheet = ["create", "get_slot_statuses", "upsert_slot_status_range", "update", "delete", "cancel"].indexOf(action) !== -1;
    var sheet = needsReservationSheet ? getReservationSheet(spreadsheet) : null;
    var slotSheet = needsSlotSheet ? getSlotStatusSheet(spreadsheet) : null;
    if (action === "consultation") {
      var consultationSheet = getConsultationSheet(spreadsheet);
      var consultationNow = new Date();
      var consultationId = createConsultationId(consultationNow);
      var consultationAttachmentUrl = saveConsultationAttachment(data, consultationId);
      consultationSheet.appendRow([
        consultationNow,
        consultationId,
        safeCell(data.service_mode),
        safeCell(data.org_name),
        safeCell(data.email),
        safeCell(data.event_date),
        safeCell(data.support_content),
        safeCell(data.instrument_value),
        safeCell(data.planning_type),
        safeCell(data.cargo_detail),
        safeCell(data.message),
        safeCell(data.attachment_name),
        safeCell(consultationAttachmentUrl)
      ]);
      var consultationAutoReplySent = sendConsultationAutoReply(data, consultationId);
      return adminActionResponse({
        ok: true,
        consultationId: consultationId,
        autoReplySent: consultationAutoReplySent
      }, requestId);
    }
    if (action === "create") {
      var now = new Date();
      var duplicate = findDuplicateReservation(sheet, data, now);
      if (duplicate) {
        return jsonResponse({
          ok: true,
          reservationId: duplicate.reservationId,
          status: duplicate.status || "確認中",
          autoReplySent: false,
          duplicate: true
        });
      }

      var activeReservationCount = countActiveReservationsByEmail(sheet, data.email, now);
      if (activeReservationCount >= MAX_ACTIVE_RESERVATIONS_PER_EMAIL) {
        return jsonResponse({
          ok: true,
          reservationId: "",
          status: "limit_reached",
          reservationLimit: true,
          maxReservations: MAX_ACTIVE_RESERVATIONS_PER_EMAIL,
          duplicate: false
        });
      }

      var preferredDate = String(data.preferred_date || "").trim();
      var preferredTime = String(data.preferred_time || "").trim();
      var durationMinutes = getLessonDuration(data.lesson_type, data.duration_minutes);
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

      var reservationId = createReservationId(now, sheet);
      sheet.appendRow([
        now,
        reservationId,
        "確認中",
        safeCell(data.name),
        safeCell(data.email),
        safeCell(data.phone),
        safeCell(data.lesson_type),
        safeCell(data.preferred_date),
        safeCell(data.preferred_time),
        safeCell(data.message),
        durationMinutes || ""
      ]);

      occupiedTimes.forEach(function (time) {
        if (time === "要相談") {
          return;
        }
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
        status: "確認中",
        autoReplySent: autoReplySent,
        duplicate: false
      });
    }

    if (action === "get_slot_statuses") {
      var from = String(data.from || "").trim();
      var to = String(data.to || "").trim();
      var slots = listSlotStatuses(slotSheet, sheet, from, to);
      var confirmedCounts = confirmedReservationCounts(sheet, slotSheet, from, to);
      return jsonResponse({ ok: true, slots: slots, confirmedCounts: confirmedCounts });
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
        createAdminSlotSource()
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
      var nextName = Object.prototype.hasOwnProperty.call(data, "name")
        ? String(data.name || "").trim()
        : currentReservation.name;
      var nextEmail = Object.prototype.hasOwnProperty.call(data, "email")
        ? String(data.email || "").trim()
        : currentReservation.email;
      var nextDurationMinutes = Object.prototype.hasOwnProperty.call(data, "duration_minutes")
        ? getLessonDuration(nextLessonType, data.duration_minutes)
        : currentReservation.durationMinutes;
      var nextSlotStatus = reservationStatusToSlotStatus(nextStatus);
      var currentTimes = reservationSlotTimes(
        currentReservation.time,
        currentReservation.durationMinutes
      );
      var nextTimes = reservationSlotTimes(nextTime, nextDurationMinutes);
      var keepsCurrentSlots = reservationSlotsMatch(
        currentReservation.date,
        currentTimes,
        nextDate,
        nextTimes
      );
      var slotConflict = nextSlotStatus === "空き" || keepsCurrentSlots
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
      if (!keepsCurrentSlots || nextSlotStatus === "空き") {
        releaseReservationSlots(slotSheet, currentReservation.date, currentTimes, reservationId);
      }
      if (nextSlotStatus !== "空き" && nextTimes[0] !== "要相談") {
        upsertSlotStatusRange(
          slotSheet,
          nextDate,
          nextDate,
          nextTimes[0],
          nextTimes[nextTimes.length - 1],
          nextSlotStatus,
          nextDurationMinutes + "分レッスン更新",
          reservationId
        );
      }
      var confirmationEmailSent = null;
      if (nextStatus === "確定" && currentReservation.status !== "確定") {
        confirmationEmailSent = sendReservationConfirmation({
          name: nextName,
          email: nextEmail,
          lesson_type: nextLessonType,
          preferred_date: nextDate,
          preferred_time: nextTime,
          duration_minutes: nextDurationMinutes
        }, reservationId);
      }
      return adminActionResponse({
        ok: true,
        reservationId: reservationId,
        status: nextStatus,
        updatedFields: updatedFields,
        confirmationEmailSent: confirmationEmailSent
      }, requestId);
    }

    if (action === "cancel") {
      var reservationId = String(data.reservation_id || "").trim();
      var email = String(data.email || "").trim().toLowerCase();
      if (!reservationId || !email) {
        return adminActionResponse({ ok: false, error: "Missing cancellation credentials" }, requestId);
      }
      var row = findReservationRowById(sheet, reservationId);
      if (!row) {
        return adminActionResponse({ ok: false, error: "NOT_FOUND" }, requestId);
      }
      var storedEmail = String(sheet.getRange(row, 5).getValue() || "").trim().toLowerCase();
      if (storedEmail !== email) {
        return adminActionResponse({ ok: false, error: "EMAIL_MISMATCH" }, requestId);
      }
      var reservation = getReservationAtRow(sheet, row);
      if (reservation.status === "キャンセル") {
        var repairedCount = releaseReservationSlots(
          slotSheet,
          reservation.date,
          reservationSlotTimes(reservation.time, reservation.durationMinutes),
          reservationId
        );
        return adminActionResponse({
          ok: true,
          reservationId: reservationId,
          status: "キャンセル",
          updatedCount: repairedCount,
          alreadyCancelled: true,
          cancellationEmailSent: null
        }, requestId);
      }
      sheet.getRange(row, 3).setValue("キャンセル");
      var releasedCount = releaseReservationSlots(
        slotSheet,
        reservation.date,
        reservationSlotTimes(reservation.time, reservation.durationMinutes),
        reservationId
      );
      var cancellationEmailSent = sendReservationCancellation({
        name: reservation.name,
        email: reservation.email,
        lesson_type: reservation.lessonType,
        preferred_date: reservation.date,
        preferred_time: reservation.time,
        duration_minutes: reservation.durationMinutes
      }, reservationId);
      return adminActionResponse({
        ok: true,
        reservationId: reservationId,
        status: "キャンセル",
        updatedCount: releasedCount,
        alreadyCancelled: false,
        cancellationEmailSent: cancellationEmailSent
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
        reservationSlotTimes(reservation.time, reservation.durationMinutes),
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

function generateTransportWorkbook(data) {
  var clientName = String(data.client_name || "").trim();
  var transportName = String(data.transport_name || "").trim();
  var editorEmail = String(data.editor_email || "").trim();
  var cargoItems = Array.isArray(data.cargo_items) ? data.cargo_items : [];
  var rateMaster = data.freight_rate_master || {};
  var instrumentMaster = data.instrument_price_master || {};
  var routeDistanceKm = Number(data.route_distance_km || 0);
  var routeProvider = String(data.route_provider || "").trim();
  var routeMeasurementSignature = String(data.route_measurement_signature || "").trim();
  var routeOrigin = String(data.route_origin || "").trim();
  var routeDestination = String(data.route_destination || "").trim();
  var totalHours = Number(data.total_hours || 0);
  var freightOperation = data.freight_operation || {};
  if (!clientName || !transportName || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(editorEmail) || !cargoItems.length || data.cargo_restrictions_agreed !== true) {
    throw new Error("Invalid transport sheet request");
  }
  if (!routeOrigin || !routeDestination || !isFinite(routeDistanceKm) || routeDistanceKm <= 0 || routeProvider !== "Google Maps" || !/^route-v1-[0-9a-f]{8}$/.test(routeMeasurementSignature) || !isFinite(totalHours) || totalHours <= 0 || !freightOperation || typeof freightOperation !== "object") {
    throw new Error("Invalid transport route request");
  }
  cargoItems.forEach(function (item) {
    var quantity = Number(item && item.quantity);
    var unitValue = Number(item && item.unit_value);
    var volumePoints = Number(item && item.volume_points);
    if (!item || typeof item !== "object" || !String(item.description || "").trim() || !isFinite(quantity) || quantity <= 0 || !isFinite(unitValue) || unitValue <= 0 || !isFinite(volumePoints) || volumePoints < 0) {
      throw new Error("Invalid transport cargo item");
    }
  });
  if (data.transport_provider_mode !== "self_light_cargo" || data.vehicle_class !== "light_cargo" || ["self_light_cargo_rate", "light_cargo_reference"].indexOf(data.pricing_basis) === -1 || rateMaster.verified !== true) {
    throw new Error("Invalid transport pricing request");
  }

  var createdAt = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyyMMdd_HHmmss");
  var workbook = SpreadsheetApp.create("輸送対象物明細_" + clientName + "_" + transportName + "_" + createdAt);
  var file = DriveApp.getFileById(workbook.getId());
  try {
  var folderId = String(PropertiesService.getScriptProperties().getProperty("TRANSPORT_DOCUMENT_FOLDER_ID") || "").trim();
  if (folderId) {
    file.moveTo(DriveApp.getFolderById(folderId));
  }
  file.addEditor(editorEmail);

  var cargoSheet = workbook.getSheets()[0];
  cargoSheet.setName("輸送対象物明細");
  var cargoHeaders = ["区分", "楽器・機材", "メーカー・型番", "数量", "状態", "評価方式", "単価・評価額", "行評価額", "容積pt/個", "容積pt合計", "備考"];
  cargoSheet.getRange(1, 1, 1, cargoHeaders.length).setValues([cargoHeaders]);
  var cargoRows = cargoItems.map(function (item) {
    var quantity = Number(item.quantity || 0);
    var volumePoints = Number(item.volume_points || 0);
    return [
      safeCell(item.category), safeCell(item.description), safeCell(item.maker_model), quantity,
      safeCell(item.condition), safeCell(item.valuation_mode), Number(item.unit_value || 0),
      Number(item.total_value || 0), volumePoints, quantity * volumePoints, safeCell(item.notes)
    ];
  });
  cargoSheet.getRange(2, 1, cargoRows.length, cargoHeaders.length).setValues(cargoRows);
  cargoSheet.getRange(2, 8, cargoRows.length, 1).setFormulaR1C1("=RC[-4]*RC[-1]");
  cargoSheet.getRange(2, 10, cargoRows.length, 1).setFormulaR1C1("=RC[-6]*RC[-1]");
  var totalRow = cargoRows.length + 2;
  cargoSheet.getRange(totalRow, 1, 1, 7).merge().setValue("申告総評価額");
  cargoSheet.getRange(totalRow, 8).setFormula("=SUM(H2:H" + (totalRow - 1) + ")");
  cargoSheet.getRange(totalRow, 9).setValue("容積ポイント合計");
  cargoSheet.getRange(totalRow, 10).setFormula("=SUM(J2:J" + (totalRow - 1) + ")");
  cargoSheet.getRange(1, 1, 1, cargoHeaders.length).setBackground("#14532d").setFontColor("#ffffff").setFontWeight("bold");
  cargoSheet.getRange("G:H").setNumberFormat("¥#,##0");
  cargoSheet.setFrozenRows(1);
  cargoSheet.autoResizeColumns(1, cargoHeaders.length);

  var mapsUrl = "https://www.google.com/maps/dir/?api=1&origin=" + encodeURIComponent(routeOrigin) + "&destination=" + encodeURIComponent(routeDestination) + "&travelmode=driving";
  var routeSheet = workbook.insertSheet("運行計画・積卸し経路図");
  var routeRows = [
    ["取引先名", safeCell(clientName)],
    ["輸送案件名", safeCell(transportName)],
    ["積込地点（出発地）", safeCell(routeOrigin)],
    ["取卸地点（目的地）", safeCell(routeDestination)],
    ["Google Maps自動算出距離", routeDistanceKm],
    ["見積反映距離", routeDistanceKm],
    ["距離算定方法", routeProvider + " Routes API自動算出距離をそのまま反映"],
    ["総拘束時間", totalHours],
    ["待機時間", Number(freightOperation.waiting_minutes || 0)],
    ["積卸し作業時間", Number(freightOperation.loading_minutes || 0)],
    ["積卸し方法", safeCell(freightOperation.loading_support_mode)]
  ];
  routeSheet.getRange("A1:D1").merge().setValue("運行計画・積卸し経路図").setBackground("#0f766e").setFontColor("#ffffff").setFontWeight("bold").setFontSize(16).setHorizontalAlignment("center");
  routeSheet.getRange("A3:D3").merge().setValue(routeOrigin + "  →  " + routeDestination).setBackground("#ecfdf5").setFontColor("#064e3b").setFontWeight("bold").setFontSize(14).setHorizontalAlignment("center").setVerticalAlignment("middle").setWrap(true);
  routeSheet.setRowHeight(3, 54);
  routeSheet.getRange("A4:D4").merge().setValue("Google Maps Routes APIで測定した自動車ルート").setFontColor("#475569").setHorizontalAlignment("center");
  routeSheet.getRange("A6:B6").merge().setValue("見積反映距離").setBackground("#dbeafe").setFontWeight("bold").setHorizontalAlignment("center");
  routeSheet.getRange("A7:B8").merge().setValue(routeDistanceKm).setNumberFormat('0.0" km"').setBackground("#eff6ff").setFontColor("#1d4ed8").setFontWeight("bold").setFontSize(22).setHorizontalAlignment("center").setVerticalAlignment("middle");
  routeSheet.getRange("C6:D6").merge().setValue("総拘束時間").setBackground("#fef3c7").setFontWeight("bold").setHorizontalAlignment("center");
  routeSheet.getRange("C7:D8").merge().setValue(totalHours).setNumberFormat('0.0" 時間"').setBackground("#fffbeb").setFontColor("#92400e").setFontWeight("bold").setFontSize(22).setHorizontalAlignment("center").setVerticalAlignment("middle");
  routeSheet.getRange("A10:B10").setValues([["運行情報", "内容"]]).setBackground("#334155").setFontColor("#ffffff").setFontWeight("bold");
  routeSheet.getRange(11, 1, routeRows.length, 2).setValues(routeRows);
  routeSheet.getRange(15, 2, 2, 1).setNumberFormat('0.0"km"');
  routeSheet.getRange(18, 2).setNumberFormat('0.0"時間"');
  routeSheet.getRange(19, 2, 2, 1).setNumberFormat('0"分"');
  routeSheet.getRange("A23:D23").merge().setFormula('=HYPERLINK("' + mapsUrl.replace(/"/g, '""') + '","▶ Google マップで実際の経路を開く")').setBackground("#2563eb").setFontColor("#ffffff").setFontWeight("bold").setFontSize(13).setHorizontalAlignment("center");
  routeSheet.getRange("A1:D23").setBorder(true, true, true, true, true, true, "#cbd5e1", SpreadsheetApp.BorderStyle.SOLID);
  routeSheet.setFrozenRows(1);
  routeSheet.setColumnWidths(1, 4, 180);

  var feeSheet = workbook.insertSheet("料金規定");
  var feeRows = [
    ["案件進行状態", safeCell(data.workflow_status)],
    ["運送実施形態", safeCell(data.transport_provider_mode)],
    ["参考車両区分", safeCell(data.vehicle_class)],
    ["料金根拠", safeCell(data.pricing_basis)],
    ["外部運送会社名", safeCell(data.carrier_name)],
    ["正式見積書URL", safeCell(data.carrier_quote_url)],
    ["正式見積取得日", safeCell(data.carrier_quote_date)],
    ["楽器再調達価格基準日", safeCell(instrumentMaster.effective_date)],
    ["楽器再調達価格出典URL", safeCell(instrumentMaster.source_url)],
    ["参考運賃基準日", safeCell(rateMaster.effective_date)],
    ["参考運賃出典URL", safeCell(rateMaster.source_url)],
    ["参考車種確認済み", rateMaster.verified === true ? "はい" : "いいえ"],
    ["参考運賃の税込区分", rateMaster.tax_included === true ? "税込" : "税抜"],
    ["軽貨物参考値の注意", data.pricing_basis === "light_cargo_reference" ? "法定運賃ではありません。2t未満の軽貨物車に限り、自社届出運賃または正式見積の確認前に参考使用します。" : "対象外"],
    ["20kmまでの距離制基本運賃", Number(rateMaster.distance_base_20 || 0)],
    ["21〜50km 1km加算", Number(rateMaster.distance_per_km_21_50 || 0)],
    ["51〜100km 1km加算", Number(rateMaster.distance_per_km_51_100 || 0)],
    ["101〜150km 1km加算", Number(rateMaster.distance_per_km_101_150 || rateMaster.distance_per_km_101_plus || 0)],
    ["151km以上 1km加算", Number(rateMaster.distance_per_km_151_plus || rateMaster.distance_per_km_101_plus || 0)],
    ["軽貨物2時間・20kmまで", Number(rateMaster.charter_2h || 0)],
    ["4時間制運賃", Number(rateMaster.charter_4h || 0)],
    ["8時間制運賃", Number(rateMaster.charter_8h || 0)],
    ["超過30分", Number(rateMaster.extra_30m || 0)],
    ["超過1時間", Number(rateMaster.extra_hour || 0)],
    ["待機30分", Number(rateMaster.waiting_per_30m || 0)],
    ["荷役基本料金", Number(rateMaster.loading_base || 0)],
    ["荷役15分", Number(rateMaster.loading_per_15m || 0)],
    ["荷役25ptごとの加算", Number(rateMaster.loading_per_25_points || 0)],
    ["休日割増率", Number(rateMaster.holiday_percent || 0)],
    ["深夜・早朝割増率", Number(rateMaster.night_percent || 0)],
    ["燃料基準価格", Number(rateMaster.fuel_reference_price || 0)],
    ["見積時燃料価格", Number(rateMaster.fuel_current_price || 0)],
    ["燃料差額1円・1kmあたり係数", Number(rateMaster.fuel_per_km_per_yen || 0)],
    ["外部2t車参考チャーター額", Number(rateMaster.external_2t_charter || 0)]
  ];
  feeSheet.getRange(1, 1, 1, 2).setValues([["項目", "設定値"]]).setBackground("#1f5f8b").setFontColor("#ffffff").setFontWeight("bold");
  feeSheet.getRange(2, 1, feeRows.length, 2).setValues(feeRows);
  feeSheet.getRange(16, 2, 14, 1).setNumberFormat("¥#,##0");
  feeSheet.getRange(30, 2, 2, 1).setNumberFormat('0"%"');
  feeSheet.getRange(32, 2, 2, 1).setNumberFormat("¥#,##0");
  feeSheet.getRange(34, 2).setNumberFormat("0.##");
  feeSheet.getRange(35, 2).setNumberFormat("¥#,##0");
  feeSheet.setFrozenRows(1);
  feeSheet.autoResizeColumns(1, 2);

  return {
    ok: true,
    cargoUrl: workbook.getUrl() + "#gid=" + cargoSheet.getSheetId(),
    routeUrl: workbook.getUrl() + "#gid=" + routeSheet.getSheetId(),
    feeUrl: workbook.getUrl() + "#gid=" + feeSheet.getSheetId()
  };
  } catch (error) {
    try {
      file.setTrashed(true);
    } catch (cleanupError) {
      Logger.log(cleanupError);
    }
    throw error;
  }
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
  if (sheet.getRange(1, HEADERS.length).getValue() !== HEADERS[HEADERS.length - 1]) {
    sheet.getRange(1, HEADERS.length).setValue(HEADERS[HEADERS.length - 1]);
    sheet.getRange(1, HEADERS.length)
      .setBackground("#0b2545")
      .setFontColor("#ffffff")
      .setFontWeight("bold");
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

function getConsultationSheet(spreadsheet) {
  var sheet = spreadsheet.getSheetByName(CONSULTATION_SHEET_NAME);
  if (!sheet) {
    sheet = spreadsheet.insertSheet(CONSULTATION_SHEET_NAME);
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(CONSULTATION_HEADERS);
    sheet.setFrozenRows(1);
  }
  sheet.getRange(1, 1, 1, CONSULTATION_HEADERS.length)
    .setValues([CONSULTATION_HEADERS])
    .setBackground("#1a56db")
    .setFontColor("#ffffff")
    .setFontWeight("bold");
  sheet.getRange("A:A").setNumberFormat("yyyy/mm/dd hh:mm:ss");
  sheet.autoResizeColumns(1, CONSULTATION_HEADERS.length);
  return sheet;
}

function saveConsultationAttachment(data, consultationId) {
  var attachmentData = String(data.attachment_data || "").trim();
  var attachmentName = String(data.attachment_name || "").trim();
  if (!attachmentData || !attachmentName) {
    return "";
  }
  var attachmentType = String(data.attachment_type || "application/octet-stream").trim();
  var folders = DriveApp.getFoldersByName("企画・輸送相談添付");
  var folder = folders.hasNext() ? folders.next() : DriveApp.createFolder("企画・輸送相談添付");
  var blob = Utilities.newBlob(
    Utilities.base64Decode(attachmentData),
    attachmentType,
    consultationId + "_" + attachmentName
  );
  return folder.createFile(blob).getUrl();
}

function createConsultationId(date) {
  var datePart = Utilities.formatDate(date, Session.getScriptTimeZone(), "yyyyMMdd");
  var propertyKey = "CONSULTATION_SEQUENCE_" + datePart;
  var properties = PropertiesService.getScriptProperties();
  var sequence = (Number(properties.getProperty(propertyKey)) || 0) + 1;
  properties.setProperty(propertyKey, String(sequence));
  return "C-" + datePart + "-" + zeroPad(sequence, 3);
}

function createReservationId(date, sheet) {
  var timeZone = Session.getScriptTimeZone();
  var datePart = Utilities.formatDate(date, timeZone, "yyyyMMdd");
  var propertyKey = "RESERVATION_SEQUENCE_" + datePart;
  var properties = PropertiesService.getScriptProperties();
  var highestSequence = Number(properties.getProperty(propertyKey)) || 0;
  var lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    var values = sheet.getRange(2, 2, lastRow - 1, 1).getValues();
    var idPattern = new RegExp("^R-" + datePart + "-(\\d+)$");
    values.forEach(function (row) {
      var match = idPattern.exec(String(row[0] || "").trim());
      if (match) {
        highestSequence = Math.max(highestSequence, Number(match[1]));
      }
    });
  }
  highestSequence += 1;
  properties.setProperty(propertyKey, String(highestSequence));
  return "R-" + datePart + "-" + zeroPad(highestSequence, 3);
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
    { key: "message", column: 10 },
    { key: "duration_minutes", column: 11 }
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
  var values = sheet.getRange(row, 3, 1, 9).getValues()[0];
  return {
    status: String(values[0] || "").trim(),
    name: String(values[1] || "").trim(),
    email: String(values[2] || "").trim(),
    lessonType: String(values[4] || "").trim(),
    date: normalizeReservationDate(values[5]),
    time: normalizeReservationTime(values[6]),
    durationMinutes: getLessonDuration(values[4], values[8])
  };
}

function listReservations(sheet) {
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return [];
  }
  var values = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
  return values.filter(function (row) {
    return String(row[2] || "").trim() !== "キャンセル";
  }).map(function (row) {
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
      duration_minutes: getLessonDuration(row[6], row[10]) || null,
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

function countActiveReservationsByEmail(sheet, emailValue, now) {
  var email = String(emailValue || "").trim().toLowerCase();
  if (!email || sheet.getLastRow() <= 1) {
    return 0;
  }
  var today = Utilities.formatDate(now, Session.getScriptTimeZone(), "yyyy-MM-dd");
  var values = sheet.getRange(2, 1, sheet.getLastRow() - 1, HEADERS.length).getValues();
  return values.reduce(function (count, row) {
    var status = String(row[2] || "").trim();
    var rowEmail = String(row[4] || "").trim().toLowerCase();
    var preferredDate = normalizeReservationDate(row[7]);
    if (status === "キャンセル" || rowEmail !== email || preferredDate < today) {
      return count;
    }
    return count + 1;
  }, 0);
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

function listSlotStatuses(sheet, reservationSheet, fromDateText, toDateText) {
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return [];
  }

  var fromDate = parseIsoDate(fromDateText);
  var toDate = parseIsoDate(toDateText);
  var values = sheet.getRange(2, 1, lastRow - 1, SLOT_HEADERS.length).getValues();
  var reservationStatuses = activeReservationSlotStatuses(reservationSheet);
  var slotsByKey = {};

  values.forEach(function (row, index) {
    var dateText = toDateText_(row[0]);
    var timeText = normalizeReservationTime(row[1]);
    if (!dateText) {
      return;
    }
    if (fromDate && dateText < fromDateText) {
      return;
    }
    if (toDate && dateText > toDateText) {
      return;
    }
    var key = dateText + "|" + timeText;
    var note = String(row[3] || "").trim();
    var status = String(row[2] || "").trim();
    if (note === "受付自動設定") {
      status = reservationStatuses[key] || "空き";
    }
    var updatedAt = slotUpdatedAt(row[4]);
    var current = slotsByKey[key];
    if (current && (current.updatedAt > updatedAt || (current.updatedAt === updatedAt && current.index > index))) {
      return;
    }
    slotsByKey[key] = {
      date: dateText,
      time: timeText,
      status: status,
      note: note,
      updatedAt: updatedAt,
      index: index
    };
  });

  return Object.keys(slotsByKey).map(function (key) {
    var slot = slotsByKey[key];
    return {
      date: slot.date,
      time: slot.time,
      status: slot.status,
      note: slot.note
    };
  });
}

function activeReservationSlotStatuses(sheet) {
  var statuses = {};
  var lastRow = sheet.getLastRow();
  if (lastRow <= 1) {
    return statuses;
  }
  var values = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
  values.forEach(function (row) {
    var reservationStatus = String(row[2] || "").trim();
    var slotStatus = reservationStatusToSlotStatus(reservationStatus);
    if (slotStatus === "空き") {
      return;
    }
    var dateText = normalizeReservationDate(row[7]);
    var startTime = normalizeReservationTime(row[8]);
    if (startTime === "要相談") {
      return;
    }
    reservationSlotTimes(startTime, getLessonDuration(row[6], row[10])).forEach(function (time) {
      statuses[dateText + "|" + time] = slotStatus;
    });
  });
  return statuses;
}

function confirmedReservationCounts(sheet, slotSheet, fromDateText, toDateText) {
  var counts = {};
  var lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    var values = sheet.getRange(2, 1, lastRow - 1, HEADERS.length).getValues();
    values.forEach(function (row) {
      if (String(row[2] || "").trim() !== "確定") {
        return;
      }
      var dateText = normalizeReservationDate(row[7]);
      if (!dateText || (fromDateText && dateText < fromDateText) || (toDateText && dateText > toDateText)) {
        return;
      }
      counts[dateText] = (counts[dateText] || 0) + 1;
    });
  }

  var slotLastRow = slotSheet.getLastRow();
  if (slotLastRow <= 1) {
    return counts;
  }
  var slotValues = slotSheet.getRange(2, 1, slotLastRow - 1, SLOT_HEADERS.length).getValues();
  var adminBookings = {};
  var legacyAdminTimes = {};
  slotValues.forEach(function (row) {
    if (String(row[2] || "").trim() !== "予約済") {
      return;
    }
    var dateText = toDateText_(row[0]);
    if (!dateText || (fromDateText && dateText < fromDateText) || (toDateText && dateText > toDateText)) {
      return;
    }
    var source = String(row[5] || "").trim();
    if (source.indexOf("admin:") !== 0 && source !== "admin") {
      return;
    }
    if (source === "admin") {
      if (!legacyAdminTimes[dateText]) {
        legacyAdminTimes[dateText] = [];
      }
      legacyAdminTimes[dateText].push(normalizeReservationTime(row[1]));
      return;
    }
    adminBookings[dateText + "|" + source] = dateText;
  });
  Object.keys(adminBookings).forEach(function (bookingKey) {
    var dateText = adminBookings[bookingKey];
    counts[dateText] = (counts[dateText] || 0) + 1;
  });
  Object.keys(legacyAdminTimes).forEach(function (dateText) {
    var minuteValues = [];
    var consultationCount = 0;
    legacyAdminTimes[dateText].forEach(function (timeText) {
      if (timeText === "要相談") {
        consultationCount = 1;
        return;
      }
      var minutes = toMinutes(timeText);
      if (minutes >= 0 && minuteValues.indexOf(minutes) === -1) {
        minuteValues.push(minutes);
      }
    });
    minuteValues.sort(function (left, right) { return left - right; });
    var blockCount = minuteValues.reduce(function (total, minutes, index) {
      return total + (index === 0 || minutes - minuteValues[index - 1] > 15 ? 1 : 0);
    }, 0);
    counts[dateText] = (counts[dateText] || 0) + blockCount + consultationCount;
  });
  return counts;
}

function createAdminSlotSource() {
  return "admin:" + Utilities.getUuid();
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
  var rowUpdatedTimes = {};
  rows.forEach(function (row, index) {
    var dateText = toDateText_(row[0]);
    var timeText = normalizeReservationTime(row[1]);
    if (dateText && timeText) {
      var key = dateText + "|" + timeText;
      var updatedAt = slotUpdatedAt(row[4]);
      if (!Object.prototype.hasOwnProperty.call(rowIndexes, key) || updatedAt >= rowUpdatedTimes[key]) {
        rowIndexes[key] = index;
        rowUpdatedTimes[key] = updatedAt;
      }
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
  var values = sheet.getRange(2, 1, lastRow - 1, 5).getValues();
  var selectedRow = 0;
  var selectedUpdatedAt = -1;
  for (var index = 0; index < values.length; index += 1) {
    if (toDateText_(values[index][0]) === dateText && normalizeReservationTime(values[index][1]) === timeText) {
      var updatedAt = slotUpdatedAt(values[index][4]);
      if (updatedAt >= selectedUpdatedAt) {
        selectedRow = index + 2;
        selectedUpdatedAt = updatedAt;
      }
    }
  }
  return selectedRow;
}

function slotUpdatedAt(value) {
  if (value instanceof Date && !isNaN(value.getTime())) {
    return value.getTime();
  }
  var parsed = new Date(value);
  return isNaN(parsed.getTime()) ? 0 : parsed.getTime();
}

function getSlotStatus(sheet, dateText, timeText) {
  return getSlotRecord(sheet, dateText, timeText).status;
}

function getLessonDuration(lessonType, explicitDuration) {
  var normalizedType = String(lessonType || "").trim();
  var configuredDuration = LESSON_DURATION_MINUTES[normalizedType];
  if (configuredDuration) {
    return configuredDuration;
  }
  var parsedDuration = Number(explicitDuration);
  if (parsedDuration >= 15 && parsedDuration <= 480 && parsedDuration % 15 === 0) {
    return parsedDuration;
  }
  return normalizedType === "グループ・部活動指導" ? 0 : 60;
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
    if (times[index] === "要相談" && slot.status !== "お休み") {
      continue;
    }
    if (slot.status && slot.status !== "空き" && slot.source !== reservationId) {
      return slot;
    }
  }
  return null;
}

function getSlotRecord(sheet, dateText, timeText) {
  var row = findSlotRow(sheet, dateText, timeText);
  if (!row) {
    return { status: "", note: "", source: "" };
  }
  var values = sheet.getRange(row, 3, 1, 4).getValues()[0];
  return {
    status: String(values[0] || "").trim(),
    note: String(values[1] || "").trim(),
    source: String(values[3] || "").trim()
  };
}

function reservationSlotsMatch(currentDate, currentTimes, nextDate, nextTimes) {
  if (currentDate !== nextDate || currentTimes.length !== nextTimes.length) {
    return false;
  }
  for (var index = 0; index < currentTimes.length; index += 1) {
    if (currentTimes[index] !== nextTimes[index]) {
      return false;
    }
  }
  return true;
}

function releaseReservationSlots(sheet, dateText, times, reservationId) {
  var releasedCount = 0;
  times.forEach(function (time) {
    var slot = getSlotRecord(sheet, dateText, time);
    var isLegacyReservationSlot = slot.note === "受付自動設定";
    if (slot.source !== reservationId && !isLegacyReservationSlot) {
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
  var email = sanitizeMailHeader(data.email).trim();
  if (!email || email.indexOf("@") <= 0) {
    return false;
  }

  var name = sanitizeMailHeader(data.name).trim() || "お客様";
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
    "所要時間: " + formatLessonDuration(lessonType, data.duration_minutes),
    "希望日: " + preferredDate,
    "希望時間: " + preferredTime,
    "現在の状態: 確認中",
    "",
    "担当より日程確定のご連絡を差し上げます。",
    "このメールは自動送信です。"
  ].join("\n");
  var htmlBody = [
    "<!doctype html>",
    '<html><head><meta charset="UTF-8"></head><body>',
    "<p>" + escapeHtml(name) + " 様</p>",
    "<p>レッスン予約のお申し込みありがとうございます。<br>",
    "以下の内容で確かに受け付けました。</p>",
    "<p>受付番号: " + escapeHtml(reservationId) + "<br>",
    "レッスン種別: " + escapeHtml(lessonType) + "<br>",
    "所要時間: " + escapeHtml(formatLessonDuration(lessonType, data.duration_minutes)) + "<br>",
    "希望日: " + escapeHtml(preferredDate) + "<br>",
    "希望時間: " + escapeHtml(preferredTime) + "<br>",
    "現在の状態: 確認中</p>",
    "<p>担当より日程確定のご連絡を差し上げます。<br>",
    "このメールは自動送信です。</p>",
    "</body></html>"
  ].join("");

  try {
    GmailApp.sendEmail(email, "【なめがわブラス・ラボ】レッスン予約受付完了", body, {
      htmlBody: htmlBody,
      name: "なめがわブラス・ラボ",
      replyTo: ADMIN_NOTIFICATION_EMAIL,
      bcc: ADMIN_NOTIFICATION_EMAIL
    });
    return true;
  } catch (error) {
    Logger.log(error);
    return false;
  }
}

function sendConsultationAutoReply(data, consultationId) {
  var email = sanitizeMailHeader(data.email).trim();
  if (!email || email.indexOf("@") <= 0) {
    return false;
  }

  var name = sanitizeMailHeader(data.org_name).trim() || "お客様";
  var serviceMode = String(data.service_mode || "").trim();
  var body = [
    name + " 様",
    "",
    "イベント企画・輸送のご相談ありがとうございます。",
    "以下の内容で確かに受け付けました。",
    "",
    "受付番号: " + consultationId,
    "サービス種別: " + serviceMode,
    "",
    "内容を確認後、担当よりお見積りと進め方をご連絡します。",
    "このメールは自動送信です。"
  ].join("\n");
  var htmlBody = [
    "<!doctype html>",
    '<html><head><meta charset="UTF-8"></head><body>',
    "<p>" + escapeHtml(name) + " 様</p>",
    "<p>イベント企画・輸送のご相談ありがとうございます。<br>",
    "以下の内容で確かに受け付けました。</p>",
    "<p>受付番号: " + escapeHtml(consultationId) + "<br>",
    "サービス種別: " + escapeHtml(serviceMode) + "</p>",
    "<p>内容を確認後、担当よりお見積りと進め方をご連絡します。<br>",
    "このメールは自動送信です。</p>",
    "</body></html>"
  ].join("");

  try {
    GmailApp.sendEmail(email, "【なめがわブラス・ラボ】企画・輸送相談受付完了", body, {
      htmlBody: htmlBody,
      name: "なめがわブラス・ラボ",
      replyTo: ADMIN_NOTIFICATION_EMAIL,
      bcc: ADMIN_NOTIFICATION_EMAIL
    });
    return true;
  } catch (error) {
    Logger.log(error);
    return false;
  }
}

function sendReservationConfirmation(data, reservationId) {
  var email = sanitizeMailHeader(data.email).trim();
  if (!email || email.indexOf("@") <= 0) {
    return false;
  }

  var name = sanitizeMailHeader(data.name).trim() || "お客様";
  var lessonType = String(data.lesson_type || "").trim();
  var confirmedDate = String(data.preferred_date || "").trim();
  var confirmedTime = String(data.preferred_time || "").trim();
  var duration = formatLessonDuration(lessonType, data.duration_minutes);
  var body = [
    name + " 様",
    "",
    "レッスン予約が確定しました。",
    "以下の日時でお待ちしております。",
    "",
    "受付番号: " + reservationId,
    "レッスン種別: " + lessonType,
    "所要時間: " + duration,
    "確定日: " + confirmedDate,
    "確定時間: " + confirmedTime,
    "現在の状態: 確定",
    "",
    "変更やキャンセルが必要な場合は、予約ページからお手続きください。",
    "このメールは自動送信です。"
  ].join("\n");
  var htmlBody = [
    "<!doctype html>",
    '<html><head><meta charset="UTF-8"></head><body>',
    "<p>" + escapeHtml(name) + " 様</p>",
    "<p><strong>レッスン予約が確定しました。</strong><br>",
    "以下の日時でお待ちしております。</p>",
    "<p>受付番号: " + escapeHtml(reservationId) + "<br>",
    "レッスン種別: " + escapeHtml(lessonType) + "<br>",
    "所要時間: " + escapeHtml(duration) + "<br>",
    "確定日: " + escapeHtml(confirmedDate) + "<br>",
    "確定時間: " + escapeHtml(confirmedTime) + "<br>",
    "現在の状態: 確定</p>",
    "<p>変更やキャンセルが必要な場合は、予約ページからお手続きください。<br>",
    "このメールは自動送信です。</p>",
    "</body></html>"
  ].join("");

  try {
    GmailApp.sendEmail(email, "【なめがわブラス・ラボ】レッスン予約確定のお知らせ", body, {
      htmlBody: htmlBody,
      name: "なめがわブラス・ラボ",
      replyTo: "zuomuj924@gmail.com"
    });
    return true;
  } catch (error) {
    Logger.log(error);
    return false;
  }
}

function sendReservationCancellation(data, reservationId) {
  var email = sanitizeMailHeader(data.email).trim();
  if (!email || email.indexOf("@") <= 0) {
    return false;
  }

  var name = sanitizeMailHeader(data.name).trim() || "お客様";
  var lessonType = String(data.lesson_type || "").trim();
  var reservationDate = String(data.preferred_date || "").trim();
  var reservationTime = String(data.preferred_time || "").trim();
  var duration = formatLessonDuration(lessonType, data.duration_minutes);
  var body = [
    name + " 様",
    "",
    "レッスン予約のキャンセルを承りました。",
    "以下の予約はキャンセル済みです。",
    "",
    "受付番号: " + reservationId,
    "レッスン種別: " + lessonType,
    "所要時間: " + duration,
    "予約日: " + reservationDate,
    "予約時間: " + reservationTime,
    "現在の状態: キャンセル",
    "",
    "キャンセルした予約枠は予約可能へ戻りました。",
    "このメールは自動送信です。"
  ].join("\n");
  var htmlBody = [
    "<!doctype html>",
    '<html><head><meta charset="UTF-8"></head><body>',
    "<p>" + escapeHtml(name) + " 様</p>",
    "<p><strong>レッスン予約のキャンセルを承りました。</strong><br>",
    "以下の予約はキャンセル済みです。</p>",
    "<p>受付番号: " + escapeHtml(reservationId) + "<br>",
    "レッスン種別: " + escapeHtml(lessonType) + "<br>",
    "所要時間: " + escapeHtml(duration) + "<br>",
    "予約日: " + escapeHtml(reservationDate) + "<br>",
    "予約時間: " + escapeHtml(reservationTime) + "<br>",
    "現在の状態: キャンセル</p>",
    "<p>キャンセルした予約枠は予約可能へ戻りました。<br>",
    "このメールは自動送信です。</p>",
    "</body></html>"
  ].join("");

  try {
    GmailApp.sendEmail(email, "【なめがわブラス・ラボ】レッスン予約キャンセル完了", body, {
      htmlBody: htmlBody,
      name: "なめがわブラス・ラボ",
      replyTo: "zuomuj924@gmail.com"
    });
    return true;
  } catch (error) {
    Logger.log(error);
    return false;
  }
}

function sanitizeMailHeader(value) {
  return String(value || "").replace(/[\r\n]+/g, " ");
}

function formatLessonDuration(lessonType, explicitDuration) {
  var durationMinutes = getLessonDuration(lessonType, explicitDuration);
  return durationMinutes ? durationMinutes + "分" : "要相談";
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
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