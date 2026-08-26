const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const Database = require('better-sqlite3');

const DATABASE_FILE = process.env.DATABASE_FILE || path.join(__dirname, '..', 'data', 'tennko.sqlite');
fs.mkdirSync(path.dirname(DATABASE_FILE), { recursive: true });

const database = new Database(DATABASE_FILE);
database.pragma('journal_mode = WAL');
database.pragma('foreign_keys = ON');
database.exec(`
  CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL DEFAULT 'company',
    created_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS licenses (
    id TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL UNIQUE,
    key_suffix TEXT NOT NULL,
    license_type TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'unused',
    redeemed_company_id TEXT REFERENCES companies(id),
    created_at TEXT NOT NULL,
    redeemed_at TEXT
  );

  CREATE TABLE IF NOT EXISTS purchases (
    stripe_session_id TEXT PRIMARY KEY,
    stripe_event_id TEXT NOT NULL,
    stripe_price_id TEXT NOT NULL,
    license_id TEXT NOT NULL REFERENCES licenses(id),
    license_type TEXT NOT NULL,
    buyer_email TEXT NOT NULL,
    encrypted_license_key TEXT NOT NULL,
    amount_total INTEGER NOT NULL,
    currency TEXT NOT NULL,
    receipt_url TEXT,
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    delivered_at TEXT
  );

  CREATE INDEX IF NOT EXISTS records_company_date_idx ON records(company_id, date);
  CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions(expires_at);
  CREATE INDEX IF NOT EXISTS licenses_status_idx ON licenses(status);
  CREATE INDEX IF NOT EXISTS purchases_delivery_idx ON purchases(delivery_status);
`);

const userColumns = database.pragma('table_info(users)');
if (!userColumns.some((column) => column.name === 'status')) {
  database.exec("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'");
}

const recordColumns = database.pragma('table_info(records)');
if (!recordColumns.some((column) => column.name === 'user_id')) {
  database.exec('ALTER TABLE records ADD COLUMN user_id TEXT REFERENCES users(id)');
  database.exec(`
    UPDATE records
    SET user_id = (
      SELECT users.id FROM users
      WHERE users.company_id = records.company_id AND users.role = 'admin'
      ORDER BY users.created_at
      LIMIT 1
    )
  `);
}
database.exec('CREATE INDEX IF NOT EXISTS records_user_date_idx ON records(user_id, date)');

const uniqueEmailIndex = database.pragma('index_list(users)').find((index) => {
  if (!index.unique) return false;
  const columns = database.pragma(`index_info('${index.name.replaceAll("'", "''")}')`);
  return columns.length === 1 && columns[0].name === 'email';
});
if (uniqueEmailIndex) {
  database.pragma('foreign_keys = OFF');
  try {
    database.transaction(() => {
      database.exec(`
        CREATE TABLE users_without_global_email_unique (
          id TEXT PRIMARY KEY,
          company_id TEXT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
          name TEXT NOT NULL,
          email TEXT NOT NULL,
          password_hash TEXT NOT NULL,
          password_salt TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'admin',
          status TEXT NOT NULL DEFAULT 'active',
          created_at TEXT NOT NULL
        );
        INSERT INTO users_without_global_email_unique
          SELECT id, company_id, name, email, password_hash, password_salt, role, status, created_at FROM users;
        DROP TABLE users;
        ALTER TABLE users_without_global_email_unique RENAME TO users;
      `);
    })();
  } finally {
    database.pragma('foreign_keys = ON');
  }
}
database.exec('CREATE UNIQUE INDEX IF NOT EXISTS users_company_email_idx ON users(company_id, email)');

const licenseColumns = database.pragma('table_info(licenses)');
if (!licenseColumns.some((column) => column.name === 'license_type')) {
  database.exec("ALTER TABLE licenses ADD COLUMN license_type TEXT NOT NULL DEFAULT 'manual'");
}

const companyColumns = database.pragma('table_info(companies)');
if (!companyColumns.some((column) => column.name === 'account_type')) {
  database.exec("ALTER TABLE companies ADD COLUMN account_type TEXT NOT NULL DEFAULT 'company'");
  database.exec(`
    UPDATE companies
    SET account_type = 'personal'
    WHERE id IN (
      SELECT redeemed_company_id FROM licenses
      WHERE license_type = 'personal' AND redeemed_company_id IS NOT NULL
    )
  `);
}

const MAX_COMPANY_USERS = 100;

function createId() {
  return crypto.randomUUID();
}

function hashPassword(password, salt = crypto.randomBytes(16).toString('hex')) {
  return {
    salt,
    hash: crypto.scryptSync(password, salt, 64).toString('hex')
  };
}

function verifyPassword(password, salt, expectedHash) {
  const actual = Buffer.from(hashPassword(password, salt).hash, 'hex');
  const expected = Buffer.from(expectedHash, 'hex');
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}

function normalizeLicenseKey(licenseKey) {
  return String(licenseKey || '').trim().toUpperCase();
}

function hashLicenseKey(licenseKey) {
  return crypto.createHash('sha256').update(normalizeLicenseKey(licenseKey)).digest('hex');
}

function createLicenseRecord(licenseType = 'manual') {
  const segments = Array.from({ length: 4 }, () => crypto.randomBytes(3).toString('hex').toUpperCase());
  const licenseKey = `TENKO-${segments.join('-')}`;
  const licenseId = createId();
  const now = new Date().toISOString();
  database.prepare(`
    INSERT INTO licenses (id, key_hash, key_suffix, license_type, status, created_at)
    VALUES (?, ?, ?, ?, 'unused', ?)
  `).run(licenseId, hashLicenseKey(licenseKey), licenseKey.slice(-6), licenseType, now);
  return { licenseId, licenseKey };
}

function createLicense(licenseType = 'manual') {
  return createLicenseRecord(licenseType).licenseKey;
}

function encryptionKey() {
  const secret = process.env.LICENSE_ENCRYPTION_KEY || '';
  if (secret.length < 32) throw new Error('LICENSE_ENCRYPTION_KEYには32文字以上の秘密値が必要です');
  return crypto.createHash('sha256').update(secret).digest();
}

function encryptLicenseKey(licenseKey) {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', encryptionKey(), iv);
  const encrypted = Buffer.concat([cipher.update(licenseKey, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, encrypted]).toString('base64');
}

function decryptLicenseKey(value) {
  const payload = Buffer.from(value, 'base64');
  const decipher = crypto.createDecipheriv('aes-256-gcm', encryptionKey(), payload.subarray(0, 12));
  decipher.setAuthTag(payload.subarray(12, 28));
  return Buffer.concat([decipher.update(payload.subarray(28)), decipher.final()]).toString('utf8');
}

const issuePurchasedLicense = database.transaction((purchase) => {
  const existing = database.prepare('SELECT * FROM purchases WHERE stripe_session_id = ?')
    .get(purchase.stripeSessionId);
  if (existing) return { ...existing, licenseKey: decryptLicenseKey(existing.encrypted_license_key) };

  const { licenseId, licenseKey } = createLicenseRecord(purchase.licenseType);
  const now = new Date().toISOString();
  database.prepare(`
    INSERT INTO purchases (
      stripe_session_id, stripe_event_id, stripe_price_id, license_id, license_type,
      buyer_email, encrypted_license_key, amount_total, currency, receipt_url, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    purchase.stripeSessionId,
    purchase.stripeEventId,
    purchase.stripePriceId,
    licenseId,
    purchase.licenseType,
    purchase.buyerEmail,
    encryptLicenseKey(licenseKey),
    purchase.amountTotal,
    purchase.currency,
    purchase.receiptUrl || null,
    now
  );
  return { ...purchase, license_id: licenseId, delivery_status: 'pending', licenseKey };
});

function markPurchaseDelivered(stripeSessionId, receiptUrl) {
  database.prepare(`
    UPDATE purchases
    SET delivery_status = 'delivered', receipt_url = COALESCE(?, receipt_url), delivered_at = ?
    WHERE stripe_session_id = ?
  `).run(receiptUrl || null, new Date().toISOString(), stripeSessionId);
}

const registerCompany = database.transaction(({ accountType, companyName, userName, email, password, licenseKey }) => {
  const now = new Date().toISOString();
  const companyId = createId();
  const userId = createId();
  const passwordData = hashPassword(password);
  const license = database.prepare(`
    SELECT id, license_type FROM licenses WHERE key_hash = ? AND status = 'unused'
  `).get(hashLicenseKey(licenseKey));

  if (!license) {
    const error = new Error('ライセンスキーが無効または使用済みです');
    error.code = 'INVALID_LICENSE';
    throw error;
  }
  if (license.license_type !== 'manual' && license.license_type !== accountType) {
    const error = new Error(license.license_type === 'personal'
      ? 'このキーは個人版です。利用区分で「個人」を選択してください'
      : 'このキーは会社版です。利用区分で「会社・事業者」を選択してください');
    error.code = 'INVALID_LICENSE';
    throw error;
  }

  database.prepare('INSERT INTO companies (id, name, account_type, created_at) VALUES (?, ?, ?, ?)')
    .run(companyId, companyName, accountType, now);
  database.prepare(`
    INSERT INTO users (id, company_id, name, email, password_hash, password_salt, role, created_at)
    VALUES (?, ?, ?, ?, ?, ?, 'admin', ?)
  `).run(userId, companyId, userName, email.toLowerCase(), passwordData.hash, passwordData.salt, now);
  database.prepare(`
    UPDATE licenses
    SET status = 'redeemed', redeemed_company_id = ?, redeemed_at = ?
    WHERE id = ? AND status = 'unused'
  `).run(companyId, now, license.id);

  return { companyId, userId };
});

function createSession(userId) {
  const token = crypto.randomBytes(32).toString('base64url');
  const tokenHash = crypto.createHash('sha256').update(token).digest('hex');
  const now = new Date();
  const expiresAt = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000).toISOString();
  database.prepare('DELETE FROM sessions WHERE expires_at <= ?').run(now.toISOString());
  database.prepare('INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)')
    .run(tokenHash, userId, expiresAt, now.toISOString());
  return { token, expiresAt };
}

function getSession(token) {
  if (!token) return null;
  const tokenHash = crypto.createHash('sha256').update(token).digest('hex');
  return database.prepare(`
    SELECT sessions.token_hash, sessions.expires_at,
      users.id AS user_id, users.name AS user_name, users.email, users.role,
      companies.id AS company_id, companies.name AS company_name, companies.account_type
    FROM sessions
    JOIN users ON users.id = sessions.user_id
    JOIN companies ON companies.id = users.company_id
    WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.status = 'active'
  `).get(tokenHash, new Date().toISOString()) || null;
}

function deleteSession(token) {
  if (!token) return;
  const tokenHash = crypto.createHash('sha256').update(token).digest('hex');
  database.prepare('DELETE FROM sessions WHERE token_hash = ?').run(tokenHash);
}

function findUserByEmail(email, accountType) {
  return database.prepare(`
    SELECT users.* FROM users
    JOIN companies ON companies.id = users.company_id
    WHERE users.email = ? AND companies.account_type = ? AND users.status = 'active'
  `).get(email.toLowerCase(), accountType) || null;
}

function listCompanyUsers(companyId) {
  return database.prepare(`
    SELECT id, name, email, role, status, created_at AS createdAt
    FROM users WHERE company_id = ?
    ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, created_at
  `).all(companyId);
}

function findCompanyUser(companyId, userId) {
  return database.prepare(`
    SELECT id, name, email, role, status, created_at AS createdAt
    FROM users WHERE company_id = ? AND id = ?
  `).get(companyId, userId) || null;
}

const createCompanyUser = database.transaction(({ companyId, name, email, password }) => {
  const company = database.prepare('SELECT account_type FROM companies WHERE id = ?').get(companyId);
  if (!company || company.account_type !== 'company') {
    const error = new Error('ユーザー追加は会社版でのみ利用できます');
    error.code = 'COMPANY_PLAN_REQUIRED';
    throw error;
  }

  const activeUsers = database.prepare("SELECT COUNT(*) AS count FROM users WHERE company_id = ? AND status = 'active'")
    .get(companyId).count;
  if (activeUsers >= MAX_COMPANY_USERS) {
    const error = new Error(`会社版は管理者を含め${MAX_COMPANY_USERS}ユーザーまでです`);
    error.code = 'USER_LIMIT_REACHED';
    throw error;
  }

  const userId = createId();
  const passwordData = hashPassword(password);
  database.prepare(`
    INSERT INTO users (id, company_id, name, email, password_hash, password_salt, role, status, created_at)
    VALUES (?, ?, ?, ?, ?, ?, 'member', 'active', ?)
  `).run(userId, companyId, name, email.toLowerCase(), passwordData.hash, passwordData.salt, new Date().toISOString());
  return userId;
});

const setCompanyUserStatus = database.transaction(({ companyId, userId, active }) => {
  const user = database.prepare('SELECT role, status FROM users WHERE id = ? AND company_id = ?')
    .get(userId, companyId);
  if (!user) return false;
  if (user.role === 'admin') {
    const error = new Error('管理者アカウントは無効化できません');
    error.code = 'ADMIN_STATUS_LOCKED';
    throw error;
  }
  if (active && user.status !== 'active') {
    const activeUsers = database.prepare("SELECT COUNT(*) AS count FROM users WHERE company_id = ? AND status = 'active'")
      .get(companyId).count;
    if (activeUsers >= MAX_COMPANY_USERS) {
      const error = new Error(`会社版は管理者を含め${MAX_COMPANY_USERS}ユーザーまでです`);
      error.code = 'USER_LIMIT_REACHED';
      throw error;
    }
  }

  database.prepare('UPDATE users SET status = ? WHERE id = ? AND company_id = ?')
    .run(active ? 'active' : 'disabled', userId, companyId);
  if (!active) database.prepare('DELETE FROM sessions WHERE user_id = ?').run(userId);
  return true;
});

const deleteCompanyUser = database.transaction(({ companyId, userId }) => {
  const user = database.prepare('SELECT role FROM users WHERE id = ? AND company_id = ?')
    .get(userId, companyId);
  if (!user) return false;
  if (user.role === 'admin') {
    const error = new Error('管理者アカウントは削除できません');
    error.code = 'ADMIN_DELETE_LOCKED';
    throw error;
  }

  return database.prepare('DELETE FROM users WHERE id = ? AND company_id = ?')
    .run(userId, companyId).changes > 0;
});

const changePassword = database.transaction((userId, currentPassword, newPassword, currentTokenHash) => {
  const user = database.prepare('SELECT password_hash, password_salt FROM users WHERE id = ?').get(userId);
  if (!user || !verifyPassword(currentPassword, user.password_salt, user.password_hash)) return false;

  const passwordData = hashPassword(newPassword);
  database.prepare('UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?')
    .run(passwordData.hash, passwordData.salt, userId);
  database.prepare('DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?')
    .run(userId, currentTokenHash);
  return true;
});

function parseRecord(row) {
  return row ? { ...JSON.parse(row.payload), id: row.id, date: row.date } : null;
}

function listRecords(companyId, userId, prefix = '') {
  const rows = prefix
    ? database.prepare('SELECT * FROM records WHERE company_id = ? AND user_id = ? AND date LIKE ? ORDER BY date').all(companyId, userId, `${prefix}%`)
    : database.prepare('SELECT * FROM records WHERE company_id = ? AND user_id = ? ORDER BY date').all(companyId, userId);
  return rows.map(parseRecord);
}

function findRecord(companyId, userId, recordId) {
  return parseRecord(database.prepare('SELECT * FROM records WHERE company_id = ? AND user_id = ? AND id = ?').get(companyId, userId, recordId));
}

function saveRecord(companyId, userId, record) {
  database.prepare(`
    INSERT INTO records (id, company_id, user_id, date, payload, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET date = excluded.date, payload = excluded.payload, updated_at = excluded.updated_at
    WHERE records.company_id = excluded.company_id AND records.user_id = excluded.user_id
  `).run(record.id, companyId, userId, record.date, JSON.stringify(record), record.updatedAt);
}

function deleteRecord(companyId, userId, recordId) {
  return database.prepare('DELETE FROM records WHERE company_id = ? AND user_id = ? AND id = ?').run(companyId, userId, recordId).changes > 0;
}

module.exports = {
  database,
  MAX_COMPANY_USERS,
  createLicense,
  issuePurchasedLicense,
  markPurchaseDelivered,
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
  deleteRecord
};