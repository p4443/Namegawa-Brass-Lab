const express = require('express');
const path = require('path');
const Stripe = require('stripe');
const {
  database,
  MAX_COMPANY_USERS,
  registerCompany,
  createSession,
  getSession,
  deleteSession,
  findUserByEmail,
  listCompanyUsers,
  findCompanyUser,
  createCompanyUser,
  setCompanyUserStatus,
  deleteCompanyUser,
  changePassword,
  verifyPassword,
  listRecords,
  findRecord,
  saveRecord,
  deleteRecord,
  issuePurchasedLicense,
  markPurchaseDelivered
} = require('./database');
const { createBackup } = require('./backup-database');
const app = express();
app.set('trust proxy', 1);
const EXCELJS_BROWSER_FILE = require.resolve('exceljs/dist/exceljs.min.js');
const PORT = process.env.PORT || 3000;
const updates = [];
const MIN_RECORD_DATE = '2026-01-01';
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
const MAX_TOTAL_ATTACHMENT_BYTES = 15 * 1024 * 1024;
const AUTH_RATE_LIMIT_WINDOW_MS = 15 * 60 * 1000;
const AUTH_RATE_LIMIT_MAX = 10;
const BACKUP_INTERVAL_MS = 24 * 60 * 60 * 1000;
const authAttempts = new Map();
const stripe = process.env.STRIPE_SECRET_KEY ? new Stripe(process.env.STRIPE_SECRET_KEY) : null;
const LICENSE_PRODUCTS = {
  personal: { priceId: process.env.STRIPE_PERSONAL_PRICE_ID, amount: 500, label: '個人版' },
  company: { priceId: process.env.STRIPE_COMPANY_PRICE_ID, amount: 50000, label: '会社版' }
};
const INVOICE_ISSUER = 'なめがわブラス・ラボ';
const INVOICE_REGISTRATION_NUMBER = 'T2810320517878';
const TAX_RATE_PERCENT = 10;
const SALES_CONFIG = [
  'STRIPE_SECRET_KEY',
  'STRIPE_WEBHOOK_SECRET',
  'STRIPE_PERSONAL_PRICE_ID',
  'STRIPE_COMPANY_PRICE_ID',
  'RESEND_API_KEY',
  'LICENSE_EMAIL_FROM',
  'LICENSE_ENCRYPTION_KEY'
];
const ALLOWED_ATTACHMENT_TYPES = new Set([
  'application/pdf',
  'image/jpeg',
  'image/png',
  'image/heic',
  'image/heif',
  'image/webp'
]);

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

async function readRawRequest(req, maxBytes = 1024 * 1024) {
  const chunks = [];
  let totalBytes = 0;
  for await (const chunk of req) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    totalBytes += buffer.length;
    if (totalBytes > maxBytes) throw new Error('Webhook本文が大きすぎます');
    chunks.push(buffer);
  }
  return Buffer.concat(chunks, totalBytes);
}

function invoiceDetails(product, purchasedAt, stripeTaxAmount) {
  const taxAmount = Number.isInteger(stripeTaxAmount)
    ? stripeTaxAmount
    : Math.floor(product.amount * TAX_RATE_PERCENT / (100 + TAX_RATE_PERCENT));
  const taxableAmount = product.amount - taxAmount;
  const transactionDate = new Intl.DateTimeFormat('ja-JP', {
    timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit'
  }).format(purchasedAt);
  return { taxAmount, taxableAmount, transactionDate };
}

async function sendLicenseEmail({ email, buyerName, licenseKey, licenseType, receiptUrl, stripeSessionId, purchasedAt, taxAmount }) {
  const appUrl = process.env.PUBLIC_APP_URL || process.env.RENDER_EXTERNAL_URL;
  if (!process.env.RESEND_API_KEY || !process.env.LICENSE_EMAIL_FROM || !appUrl) {
    throw new Error('メール送信設定（RESEND_API_KEY、LICENSE_EMAIL_FROM、PUBLIC_APP_URL）が不足しています');
  }
  const product = LICENSE_PRODUCTS[licenseType];
  const invoice = invoiceDetails(product, purchasedAt, taxAmount);
  const total = product.amount.toLocaleString('ja-JP');
  const taxable = invoice.taxableAmount.toLocaleString('ja-JP');
  const tax = invoice.taxAmount.toLocaleString('ja-JP');
  const customer = buyerName || email;
  const invoiceText = `\n\n適格請求書情報\n発行者: ${INVOICE_ISSUER}\n登録番号: ${INVOICE_REGISTRATION_NUMBER}\n取引日: ${invoice.transactionDate}\n購入者: ${customer}\n取引内容: 点呼確認簿 ${product.label}\n合計: ${total}円（税込）\n10%対象額: ${taxable}円（税抜）\n消費税額（10%）: ${tax}円\n決済番号: ${stripeSessionId}`;
  const invoiceHtml = `<hr><h2>適格請求書情報</h2><p>発行者: ${INVOICE_ISSUER}<br>登録番号: ${INVOICE_REGISTRATION_NUMBER}<br>取引日: ${invoice.transactionDate}<br>購入者: ${escapeHtml(customer)}<br>取引内容: 点呼確認簿 ${product.label}<br>合計: ${total}円（税込）<br>10%対象額: ${taxable}円（税抜）<br>消費税額（10%）: ${tax}円<br>決済番号: ${escapeHtml(stripeSessionId)}</p>`;
  const receiptText = receiptUrl ? `\n領収書: ${receiptUrl}` : '';
  const receiptHtml = receiptUrl
    ? `<p><a href="${escapeHtml(receiptUrl)}">Stripeの領収書を表示</a></p>`
    : '<p>Stripeから送信される領収書メールもご確認ください。</p>';
  const response = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${process.env.RESEND_API_KEY}`,
      'Content-Type': 'application/json',
      'Idempotency-Key': `license-${stripeSessionId}`
    },
    body: JSON.stringify({
      from: process.env.LICENSE_EMAIL_FROM,
      to: [email],
      subject: `点呼確認簿 ${product.label} ライセンスキー`,
      text: `点呼確認簿をご購入いただきありがとうございます。\n\n商品: ${product.label}\n利用URL: ${appUrl}\nライセンスキー: ${licenseKey}\n\n利用URLを開き「アカウントを新規登録」から登録してください。${invoiceText}${receiptText}\n`,
      html: `<h1>点呼確認簿 ${product.label}</h1><p>ご購入いただきありがとうございます。</p><p>利用URL: <a href="${escapeHtml(appUrl)}">${escapeHtml(appUrl)}</a></p><p>ライセンスキー:</p><p><strong>${escapeHtml(licenseKey)}</strong></p><p>利用URLを開き「アカウントを新規登録」から登録してください。</p>${invoiceHtml}${receiptHtml}`
    })
  });
  if (!response.ok) throw new Error(`メール送信に失敗しました: ${response.status} ${await response.text()}`);
}

async function fulfillStripeCheckout(event) {
  const session = event.data.object;
  if (session.payment_status !== 'paid') return;

  const lineItems = await stripe.checkout.sessions.listLineItems(session.id, { limit: 10 });
  const matched = Object.entries(LICENSE_PRODUCTS).find(([, product]) => (
    product.priceId && lineItems.data.some((item) => item.price?.id === product.priceId)
  ));
  if (!matched) throw new Error(`未登録のStripe価格です: ${session.id}`);

  const [licenseType, product] = matched;
  if (session.currency !== 'jpy' || session.amount_total !== product.amount) {
    throw new Error(`Stripe決済金額が商品設定と一致しません: ${session.id}`);
  }
  const buyerEmail = session.customer_details?.email || session.customer_email;
  if (!buyerEmail) throw new Error(`購入者メールアドレスがありません: ${session.id}`);

  let receiptUrl = null;
  if (session.payment_intent) {
    const paymentIntent = await stripe.paymentIntents.retrieve(session.payment_intent, {
      expand: ['latest_charge']
    });
    receiptUrl = typeof paymentIntent.latest_charge === 'object'
      ? paymentIntent.latest_charge.receipt_url
      : null;
  }

  const purchase = issuePurchasedLicense({
    stripeSessionId: session.id,
    stripeEventId: event.id,
    stripePriceId: product.priceId,
    licenseType,
    buyerEmail,
    amountTotal: session.amount_total,
    currency: session.currency,
    receiptUrl
  });
  if (purchase.delivery_status === 'delivered') return;

  await sendLicenseEmail({
    email: buyerEmail,
    buyerName: session.customer_details?.name,
    licenseKey: purchase.licenseKey,
    licenseType,
    receiptUrl,
    stripeSessionId: session.id,
    purchasedAt: new Date(session.created * 1000),
    taxAmount: session.total_details?.amount_tax
  });
  markPurchaseDelivered(session.id, receiptUrl);
}

app.post('/api/stripe/webhook', async (req, res) => {
  if (!stripe || !process.env.STRIPE_WEBHOOK_SECRET) {
    return res.status(503).json({ message: 'Stripeが設定されていません' });
  }

  let event;
  try {
    const rawBody = await readRawRequest(req);
    event = stripe.webhooks.constructEvent(
      rawBody,
      req.headers['stripe-signature'],
      process.env.STRIPE_WEBHOOK_SECRET
    );
  } catch (error) {
    return res.status(400).json({ message: `Webhook署名を確認できません: ${error.message}` });
  }

  try {
    if (event.type === 'checkout.session.completed' || event.type === 'checkout.session.async_payment_succeeded') {
      await fulfillStripeCheckout(event);
    }
    res.json({ received: true });
  } catch (error) {
    console.error('Stripe購入処理エラー:', error);
    res.status(500).json({ message: '購入処理を完了できませんでした' });
  }
});

// 撮影画像やPDFをJSONで受け取れるよう上限を拡張する
app.use(express.json({ limit: '25mb' }));
app.use('/api', (req, res, next) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  next();
});

app.get('/', (req, res) => {
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Expires', '0');
  res.sendFile(path.join(__dirname, '..', 'index.html'));
});

app.get('/vendor/exceljs.min.js', (req, res) => {
  res.type('application/javascript');
  res.sendFile(EXCELJS_BROWSER_FILE);
});

app.get('/terms', (req, res) => {
  res.sendFile(path.join(__dirname, '..', 'TERMS_OF_USE.md'));
});

app.get('/privacy', (req, res) => {
  res.sendFile(path.join(__dirname, '..', 'PRIVACY_POLICY.md'));
});

app.get('/legal-notice', (req, res) => {
  res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate');
  res.sendFile(path.join(__dirname, '..', 'SPECIFIED_COMMERCIAL_TRANSACTIONS.html'));
});

app.get('/api/health', (req, res) => {
  try {
    database.prepare('SELECT 1').get();
    res.json({ status: 'ok' });
  } catch (error) {
    console.error('ヘルスチェックエラー:', error);
    res.status(503).json({ status: 'unavailable' });
  }
});

app.get('/api/sales/health', (req, res) => {
  const missing = SALES_CONFIG.filter((name) => !process.env[name]);
  if (!process.env.PUBLIC_APP_URL && !process.env.RENDER_EXTERNAL_URL) missing.push('PUBLIC_APP_URL');
  res.status(missing.length ? 503 : 200).json(missing.length
    ? { status: 'configuration_required', missing }
    : { status: 'ok' });
});

function parseCookies(header = '') {
  return Object.fromEntries(header.split(';').map((part) => {
    const [name, ...value] = part.trim().split('=');
    return [name, decodeURIComponent(value.join('='))];
  }).filter(([name]) => name));
}

function sessionCookie(token) {
  const secure = process.env.NODE_ENV === 'production' ? '; Secure' : '';
  return `tennko_session=${encodeURIComponent(token)}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800${secure}`;
}

function clearSessionCookie() {
  const secure = process.env.NODE_ENV === 'production' ? '; Secure' : '';
  return `tennko_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0${secure}`;
}

function publicSession(session) {
  return {
    user: { id: session.user_id, name: session.user_name, email: session.email, role: session.role },
    company: {
      id: session.company_id,
      name: session.company_name,
      accountType: session.account_type,
      userLimit: session.account_type === 'company' ? MAX_COMPANY_USERS : 1
    }
  };
}

function requireAuth(req, res, next) {
  const token = parseCookies(req.headers.cookie).tennko_session;
  const session = getSession(token);
  if (!session) return res.status(401).json({ message: 'ログインが必要です' });
  req.sessionToken = token;
  req.auth = session;
  next();
}

function requireCompanyAdmin(req, res, next) {
  if (req.auth.account_type !== 'company') {
    return res.status(403).json({ message: 'ユーザー管理は会社版でのみ利用できます' });
  }
  if (req.auth.role !== 'admin') {
    return res.status(403).json({ message: '管理者のみ操作できます' });
  }
  next();
}

function resolveRecordUser(req, res) {
  const requestedUserId = String(req.query.userId || req.body?.userId || req.auth.user_id);
  if (requestedUserId === req.auth.user_id) return req.auth.user_id;
  if (req.auth.account_type !== 'company' || req.auth.role !== 'admin') {
    res.status(403).json({ message: '他の参加者の記録は管理者のみ管理できます' });
    return null;
  }
  const user = findCompanyUser(req.auth.company_id, requestedUserId);
  if (!user || user.status !== 'active') {
    res.status(404).json({ message: '参加者が見つかりません' });
    return null;
  }
  return user.id;
}

function limitAuthAttempts(req, res, next) {
  const now = Date.now();
  const key = req.ip || 'unknown';
  const current = authAttempts.get(key);
  const attempt = !current || current.resetAt <= now
    ? { count: 0, resetAt: now + AUTH_RATE_LIMIT_WINDOW_MS }
    : current;

  if (attempt.count >= AUTH_RATE_LIMIT_MAX) {
    res.setHeader('Retry-After', Math.ceil((attempt.resetAt - now) / 1000));
    return res.status(429).json({ message: '試行回数が多すぎます。しばらく待ってからお試しください' });
  }

  attempt.count += 1;
  authAttempts.set(key, attempt);
  next();
}

function clearAuthAttempts(req) {
  authAttempts.delete(req.ip || 'unknown');
}

app.get('/api/auth/status', (req, res) => {
  const token = parseCookies(req.headers.cookie).tennko_session;
  const session = getSession(token);
  if (!session) return res.json({ authenticated: false });
  res.json({ authenticated: true, ...publicSession(session) });
});

app.post('/api/auth/register', limitAuthAttempts, (req, res) => {
  const accountType = req.body.accountType === 'personal' ? 'personal' : 'company';
  const companyName = String(req.body.companyName || '').trim();
  const userName = String(req.body.userName || '').trim();
  const email = String(req.body.email || '').trim().toLowerCase();
  const password = String(req.body.password || '');
  const licenseKey = String(req.body.licenseKey || '').trim();

  if (!companyName || !userName || !/^\S+@\S+\.\S+$/.test(email) || !licenseKey) {
    return res.status(400).json({ message: '会社名・個人名、氏名、メールアドレス、ライセンスキーを入力してください' });
  }
  if (password.length < 8) return res.status(400).json({ message: 'パスワードは8文字以上にしてください' });

  try {
    const { userId } = registerCompany({ accountType, companyName, userName, email, password, licenseKey });
    const createdSession = createSession(userId);
    const session = getSession(createdSession.token);
    clearAuthAttempts(req);
    res.setHeader('Set-Cookie', sessionCookie(createdSession.token));
    res.status(201).json({ authenticated: true, ...publicSession(session) });
  } catch (error) {
    if (error.code === 'INVALID_LICENSE') {
      return res.status(400).json({ message: error.message });
    }
    if (error.code === 'SQLITE_CONSTRAINT_UNIQUE') {
      return res.status(409).json({ message: 'このメールアドレスは登録済みです' });
    }
    console.error('会社登録エラー:', error);
    res.status(500).json({ message: '会社を登録できませんでした' });
  }
});

app.post('/api/auth/login', limitAuthAttempts, (req, res) => {
  const accountType = req.body.accountType === 'personal' ? 'personal' : 'company';
  const email = String(req.body.email || '').trim().toLowerCase();
  const password = String(req.body.password || '');
  const user = findUserByEmail(email, accountType);

  if (!user || !verifyPassword(password, user.password_salt, user.password_hash)) {
    return res.status(401).json({ message: 'メールアドレスまたはパスワードが違います' });
  }

  const createdSession = createSession(user.id);
  const session = getSession(createdSession.token);
  clearAuthAttempts(req);
  res.setHeader('Set-Cookie', sessionCookie(createdSession.token));
  res.json({ authenticated: true, ...publicSession(session) });
});

app.post('/api/auth/logout', (req, res) => {
  const token = parseCookies(req.headers.cookie).tennko_session;
  deleteSession(token);
  res.setHeader('Set-Cookie', clearSessionCookie());
  res.status(204).end();
});

app.post('/api/auth/password', requireAuth, limitAuthAttempts, (req, res) => {
  const currentPassword = String(req.body.currentPassword || '');
  const newPassword = String(req.body.newPassword || '');

  if (newPassword.length < 8) {
    return res.status(400).json({ message: '新しいパスワードは8文字以上にしてください' });
  }
  if (currentPassword === newPassword) {
    return res.status(400).json({ message: '現在と異なるパスワードを指定してください' });
  }
  if (!changePassword(req.auth.user_id, currentPassword, newPassword, req.auth.token_hash)) {
    return res.status(400).json({ message: '現在のパスワードが違います' });
  }

  clearAuthAttempts(req);
  res.json({ message: 'パスワードを変更しました' });
});

app.get('/api/users', requireAuth, requireCompanyAdmin, (req, res) => {
  res.json({ users: listCompanyUsers(req.auth.company_id), limit: MAX_COMPANY_USERS });
});

app.post('/api/users', requireAuth, requireCompanyAdmin, limitAuthAttempts, (req, res) => {
  const name = String(req.body.name || '').trim();
  const email = String(req.body.email || '').trim().toLowerCase();
  const password = String(req.body.password || '');
  if (!name || name.length > 100 || !/^\S+@\S+\.\S+$/.test(email)) {
    return res.status(400).json({ message: '氏名と正しいメールアドレスを入力してください' });
  }
  if (password.length < 8) return res.status(400).json({ message: '仮パスワードは8文字以上にしてください' });

  try {
    const userId = createCompanyUser({ companyId: req.auth.company_id, name, email, password });
    clearAuthAttempts(req);
    const user = listCompanyUsers(req.auth.company_id).find((item) => item.id === userId);
    res.status(201).json({ user, limit: MAX_COMPANY_USERS });
  } catch (error) {
    if (error.code === 'SQLITE_CONSTRAINT_UNIQUE') {
      return res.status(409).json({ message: 'このメールアドレスは登録済みです' });
    }
    if (error.code === 'COMPANY_PLAN_REQUIRED' || error.code === 'USER_LIMIT_REACHED') {
      return res.status(400).json({ message: error.message });
    }
    console.error('ユーザー追加エラー:', error);
    res.status(500).json({ message: 'ユーザーを追加できませんでした' });
  }
});

app.patch('/api/users/:id/status', requireAuth, requireCompanyAdmin, (req, res) => {
  if (typeof req.body.active !== 'boolean') {
    return res.status(400).json({ message: '有効状態を指定してください' });
  }
  try {
    if (!setCompanyUserStatus({
      companyId: req.auth.company_id,
      userId: req.params.id,
      active: req.body.active
    })) return res.status(404).json({ message: 'ユーザーが見つかりません' });
    res.json({ message: req.body.active ? 'ユーザーを有効にしました' : 'ユーザーを無効にしました' });
  } catch (error) {
    if (error.code === 'ADMIN_STATUS_LOCKED' || error.code === 'USER_LIMIT_REACHED') {
      return res.status(400).json({ message: error.message });
    }
    console.error('ユーザー状態変更エラー:', error);
    res.status(500).json({ message: 'ユーザー状態を変更できませんでした' });
  }
});

app.delete('/api/users/:id', requireAuth, requireCompanyAdmin, (req, res) => {
  try {
    if (!deleteCompanyUser({ companyId: req.auth.company_id, userId: req.params.id })) {
      return res.status(404).json({ message: 'ユーザーが見つかりません' });
    }
    res.status(204).end();
  } catch (error) {
    if (error.code === 'ADMIN_DELETE_LOCKED') {
      return res.status(400).json({ message: error.message });
    }
    console.error('ユーザー削除エラー:', error);
    res.status(500).json({ message: 'ユーザーを削除できませんでした' });
  }
});

function attachmentOwnerId(attachment, recordUserId) {
  return attachment.uploadedByUserId || recordUserId;
}

function normalizeAttachments(attachments, existingAttachments = [], actorUserId, recordUserId) {
  const requestedAttachments = Array.isArray(attachments) ? attachments : [];
  const protectedAttachments = existingAttachments.filter((attachment) => (
    attachmentOwnerId(attachment, recordUserId) !== actorUserId
  ));
  const normalized = requestedAttachments.map((attachment) => {
    const existing = existingAttachments.find((item) => item.id === attachment.id);
    if (existing) {
      if (attachmentOwnerId(existing, recordUserId) !== actorUserId || !attachment.data) return existing;
    }

    const match = String(attachment.data || '').match(/^data:([^;]+);base64,(.+)$/);
    if (!match || !ALLOWED_ATTACHMENT_TYPES.has(match[1])) {
      throw new Error('添付できるのは画像またはPDFです');
    }
    const size = Buffer.byteLength(match[2], 'base64');
    if (size > MAX_ATTACHMENT_BYTES) throw new Error('添付ファイルは1件10MB以下にしてください');

    return {
      id: attachment.id || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      name: path.basename(String(attachment.name || 'attachment')),
      type: match[1],
      purpose: ['beforeAlcohol', 'afterAlcohol'].includes(attachment.purpose) ? attachment.purpose : 'other',
      size,
      data: match[2],
      uploadedByUserId: actorUserId
    };
  });

  protectedAttachments.forEach((attachment) => {
    if (!normalized.some((item) => item.id === attachment.id)) normalized.push(attachment);
  });

  if (normalized.reduce((total, attachment) => total + attachment.size, 0) > MAX_TOTAL_ATTACHMENT_BYTES) {
    throw new Error('添付ファイルの合計は15MB以下にしてください');
  }
  return normalized;
}

function normalizeRecord(data, existingId, existingAttachments = [], actorUserId, recordUserId) {
  const startMeter = Number(data.startMeter || 0);
  const endMeter = Number(data.endMeter || 0);

  return {
    id: existingId || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    date: String(data.date || ''),
    vehicleNumber: String(data.vehicleNumber || '').trim(),
    driver: String(data.driver || '').trim(),
    beforeMethod: String(data.beforeMethod || ''),
    beforeTime: String(data.beforeTime || ''),
    beforeDetector: String(data.beforeDetector || ''),
    beforeAlcohol: String(data.beforeAlcohol || ''),
    health: String(data.health || '良好'),
    beforeOther: String(data.beforeOther || '').trim(),
    beforeConfirmed: data.beforeConfirmed === '❌' || data.beforeConfirmed === '×' ? '×' : '○',
    afterMethod: String(data.afterMethod || ''),
    afterTime: String(data.afterTime || ''),
    afterDetector: String(data.afterDetector || ''),
    afterAlcohol: String(data.afterAlcohol || ''),
    statusReport: String(data.statusReport || '特になし'),
    afterOther: String(data.afterOther || '').trim(),
    afterConfirmed: data.afterConfirmed === '❌' || data.afterConfirmed === '×' ? '×' : '○',
    startMeter,
    breakHours: Number(data.breakHours ?? 0),
    endMeter,
    dailyDistance: Math.max(0, endMeter - startMeter),
    attachments: normalizeAttachments(data.attachments, existingAttachments, actorUserId, recordUserId),
    updatedAt: new Date().toISOString()
  };
}

function validateRecord(record) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(record.date) || record.date < MIN_RECORD_DATE) {
    return '日付は2026年1月1日以降を指定してください';
  }
  if (!record.vehicleNumber || !record.driver) {
    return '車両（車番）と運転者は必須です';
  }
  if (record.endMeter < record.startMeter) {
    return '乗務終了メーターは乗務開始メーター以上にしてください';
  }
  if (record.breakHours < 0) {
    return '休憩時間は0時間以上を指定してください';
  }
  return null;
}

function publicRecord(record, actorUserId, recordUserId, actorRole) {
  return {
    ...record,
    attachments: (record.attachments || []).map(({ data, uploadedByUserId, ...attachment }) => ({
      ...attachment,
      canView: actorRole === 'admin' || attachmentOwnerId({ uploadedByUserId }, recordUserId) === actorUserId,
      canManage: attachmentOwnerId({ uploadedByUserId }, recordUserId) === actorUserId
    }))
  };
}

app.get('/api/records', requireAuth, (req, res) => {
  const userId = resolveRecordUser(req, res);
  if (!userId) return;
  const month = String(req.query.month || '');
  const year = String(req.query.year || '');
  const prefix = month ? `${month}-` : year ? `${year}-` : '';
  res.json(listRecords(req.auth.company_id, userId, prefix)
    .map((record) => publicRecord(record, req.auth.user_id, userId, req.auth.role)));
});

app.get('/api/records/:id/attachments/:attachmentId', requireAuth, (req, res) => {
  const userId = resolveRecordUser(req, res);
  if (!userId) return;
  const record = findRecord(req.auth.company_id, userId, req.params.id);
  const attachment = record?.attachments?.find((item) => item.id === req.params.attachmentId);
  if (!attachment) return res.status(404).json({ message: '添付ファイルが見つかりません' });
  if (req.auth.role !== 'admin' && attachmentOwnerId(attachment, userId) !== req.auth.user_id) {
    return res.status(403).json({ message: 'この添付ファイルを開く権限がありません' });
  }

  res.type(attachment.type);
  res.setHeader('Content-Disposition', `inline; filename*=UTF-8''${encodeURIComponent(attachment.name)}`);
  res.send(Buffer.from(attachment.data, 'base64'));
});

app.post('/api/records', requireAuth, (req, res) => {
  const userId = resolveRecordUser(req, res);
  if (!userId) return;
  let record;
  try {
    record = normalizeRecord(req.body, null, [], req.auth.user_id, userId);
  } catch (error) {
    return res.status(400).json({ message: error.message });
  }
  const error = validateRecord(record);
  if (error) return res.status(400).json({ message: error });

  saveRecord(req.auth.company_id, userId, record);
  res.status(201).json(publicRecord(record, req.auth.user_id, userId, req.auth.role));
});

app.put('/api/records/:id', requireAuth, (req, res) => {
  const userId = resolveRecordUser(req, res);
  if (!userId) return;
  const existingRecord = findRecord(req.auth.company_id, userId, req.params.id);
  if (!existingRecord) return res.status(404).json({ message: '記録が見つかりません' });

  let record;
  try {
    record = normalizeRecord(
      req.body,
      req.params.id,
      existingRecord.attachments,
      req.auth.user_id,
      userId
    );
  } catch (error) {
    return res.status(400).json({ message: error.message });
  }
  const error = validateRecord(record);
  if (error) return res.status(400).json({ message: error });

  saveRecord(req.auth.company_id, userId, record);
  res.json(publicRecord(record, req.auth.user_id, userId, req.auth.role));
});

app.delete('/api/records/:id', requireAuth, (req, res) => {
  const userId = resolveRecordUser(req, res);
  if (!userId) return;
  const record = findRecord(req.auth.company_id, userId, req.params.id);
  if (!record) return res.status(404).json({ message: '記録が見つかりません' });
  const hasProtectedAttachments = (record.attachments || []).some((attachment) => (
    attachmentOwnerId(attachment, userId) !== req.auth.user_id
  ));
  if (hasProtectedAttachments) {
    return res.status(403).json({ message: '他のユーザーがアップロードした添付を含むため、この記録は削除できません' });
  }
  if (!deleteRecord(req.auth.company_id, userId, req.params.id)) {
    return res.status(404).json({ message: '記録が見つかりません' });
  }
  res.status(204).end();
});

// GASからのWebhookを受け取るエンドポイント
app.post('/api/sheet-update', requireAuth, (req, res) => {
  const data = req.body;

  if (!data.sheetName || !Number.isInteger(data.row) || !Number.isInteger(data.col)) {
    return res.status(400).json({ message: 'sheetName、row、colは必須です' });
  }

  const update = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    companyId: req.auth.company_id,
    sheetName: String(data.sheetName),
    row: data.row,
    col: data.col,
    selectedValue: String(data.selectedValue || ''),
    timeValue: String(data.timeValue || ''),
    actionType: data.actionType === 'CLEARED' ? 'CLEARED' : 'UPDATED',
    timestamp: data.timestamp || new Date().toISOString()
  };

  updates.unshift(update);
  updates.splice(100);
  
  console.log("=== スプレッドシートから更新を受信 ===");
  console.log(`シート名: ${update.sheetName}`);
  console.log(`行: ${update.row}, 列: ${update.col}`);
  console.log(`アクション: ${update.actionType}`);
  console.log(`選択内容: ${update.selectedValue}`);
  console.log(`記録時間: ${update.timeValue}`);
  console.log("======================================");

  // ※ここでデータベース(PostgreSQL等)への保存や、Slack通知などの処理を実装できます

  res.status(201).json(update);
});

app.get('/api/updates', requireAuth, (req, res) => {
  res.json(updates.filter((update) => update.companyId === req.auth.company_id));
});

const server = app.listen(PORT, () => {
  console.log(`Server is running on port ${PORT}`);
});

async function runScheduledBackup() {
  try {
    const backupFile = await createBackup();
    console.log(`Database backup created: ${backupFile}`);
  } catch (error) {
    console.error('データベースバックアップエラー:', error);
  }
}

runScheduledBackup();
const backupTimer = setInterval(runScheduledBackup, BACKUP_INTERVAL_MS);
backupTimer.unref();

function shutdown(signal) {
  console.log(`${signal} received, shutting down`);
  clearInterval(backupTimer);
  server.close(() => {
    database.close();
    process.exit(0);
  });
  setTimeout(() => process.exit(1), 10000).unref();
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
