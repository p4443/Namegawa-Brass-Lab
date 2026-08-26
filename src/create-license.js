const { createLicense } = require('./database');

const quantity = Number.parseInt(process.argv[2] || '1', 10);

if (!Number.isInteger(quantity) || quantity < 1 || quantity > 100) {
  console.error('発行数は1〜100を指定してください');
  process.exit(1);
}

for (let index = 0; index < quantity; index += 1) {
  console.log(createLicense());
}