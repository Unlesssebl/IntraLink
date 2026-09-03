import test from 'node:test';
import assert from 'node:assert';

function parseHostList(hostStr?: string): string[] {
  if (!hostStr) return [];
  return hostStr
    .split(/[,;\s/]+/)
    .map((h: string) => h.trim())
    .filter(Boolean);
}

function formatSla(deadline: Date, now: number = Date.now()): string {
  const ms = deadline.getTime() - now;
  if (ms < 0) return 'Просрочена';
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  if (h > 24) return `${Math.floor(h / 24)}д ${h % 24}ч`;
  if (h > 0) return `${h}ч ${m}м`;
  return `${m}м`;
}

function normalizeHostForWinRm(host: string): string {
  const clean = host.trim().replace(/^https?:\/\//i, '').replace(/:\d+$/, '');
  return clean.toUpperCase();
}

test('parseHostList: корректно извлекает несколько хостов через запятые, пробелы и слеши', () => {
  const input = 'ws-fin-01, WS-FIN-02 / ws-fin-03; 192.168.1.50';
  const hosts = parseHostList(input);
  assert.deepStrictEqual(hosts, ['ws-fin-01', 'WS-FIN-02', 'ws-fin-03', '192.168.1.50']);
});

test('parseHostList: пустая строка возвращает пустой массив', () => {
  assert.deepStrictEqual(parseHostList(''), []);
  assert.deepStrictEqual(parseHostList(undefined), []);
});

test('formatSla: корректно форматирует оставшееся время', () => {
  const now = 1000000000000;
  // Просрочено
  assert.strictEqual(formatSla(new Date(now - 60000), now), 'Просрочена');
  // 45 минут
  assert.strictEqual(formatSla(new Date(now + 45 * 60000), now), '45м');
  // 2 часа 30 минут
  assert.strictEqual(formatSla(new Date(now + 150 * 60000), now), '2ч 30м');
  // 2 дня 3 часа
  assert.strictEqual(formatSla(new Date(now + (48 + 3) * 3600000), now), '2д 3ч');
});

test('normalizeHostForWinRm: очищает URL префиксы и порты, приводя к верхнему регистру', () => {
  assert.strictEqual(normalizeHostForWinRm('http://pc-admin-01:5985'), 'PC-ADMIN-01');
  assert.strictEqual(normalizeHostForWinRm('ws-user-05'), 'WS-USER-05');
});
