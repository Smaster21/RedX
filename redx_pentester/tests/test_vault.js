const { vaultCreate, vaultExists } = require('../vault');

test('Vault creation and existence', async () => {
  const apiKey = 'test-api-key';
  const password = 'test-password';

  await vaultCreate(apiKey, password);
  expect(vaultExists()).toBe(true);
});