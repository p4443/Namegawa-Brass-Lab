const fs = require('fs');
const path = require('path');
const { database } = require('./database');

const databaseFile = process.env.DATABASE_FILE || path.join(__dirname, '..', 'data', 'tennko.sqlite');
const backupDirectory = process.env.BACKUP_DIRECTORY || path.join(path.dirname(databaseFile), 'backups');
const BACKUP_RETENTION_COUNT = 14;

function removeBackupArtifacts(backupFile, includeDatabase = false) {
  [`${backupFile}-shm`, `${backupFile}-wal`].forEach((file) => fs.rmSync(file, { force: true }));
  if (includeDatabase) fs.rmSync(backupFile, { force: true });
}

async function createBackup() {
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
  const backupFile = path.join(backupDirectory, `tennko-${timestamp}.sqlite`);
  fs.mkdirSync(backupDirectory, { recursive: true });
  await database.backup(backupFile);

  const integrity = new (require('better-sqlite3'))(backupFile, { readonly: true });
  const result = integrity.pragma('integrity_check', { simple: true });
  integrity.close();
  removeBackupArtifacts(backupFile);

  if (result !== 'ok') {
    removeBackupArtifacts(backupFile, true);
    throw new Error(`バックアップの整合性確認に失敗しました: ${result}`);
  }

  const backups = fs.readdirSync(backupDirectory)
    .filter((name) => /^tennko-.*\.sqlite$/.test(name))
    .sort()
    .reverse();
  backups.forEach((name) => removeBackupArtifacts(path.join(backupDirectory, name)));
  backups.slice(BACKUP_RETENTION_COUNT).forEach((name) => {
    removeBackupArtifacts(path.join(backupDirectory, name), true);
  });

  return backupFile;
}

if (require.main === module) {
  createBackup()
    .then((backupFile) => console.log(backupFile))
    .then(() => database.close())
    .catch((error) => {
      console.error(error.message);
      database.close();
      process.exitCode = 1;
    });
}

module.exports = { createBackup };