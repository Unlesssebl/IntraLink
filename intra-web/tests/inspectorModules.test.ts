import test from 'node:test';
import assert from 'node:assert';
import {
  captureInitialDecisionVersion,
  isDecisionVersionStale,
} from '../src/components/inspector/decisionStaleness.ts';

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

test('decision staleness: версия предыдущей заявки не переносится на новую', () => {
  const previous = { taskId: 101, version: 4 };
  const unchanged = captureInitialDecisionVersion(previous, 202, 101, 9);
  assert.deepStrictEqual(unchanged, previous);
  assert.strictEqual(isDecisionVersionStale(false, unchanged, 202, 9), false);

  const current = captureInitialDecisionVersion(unchanged, 202, 202, 2);
  assert.deepStrictEqual(current, { taskId: 202, version: 2 });
  assert.strictEqual(isDecisionVersionStale(false, current, 202, 3), true);
});

test('taskDetailsCache: сохранение, получение из кэша и инвалидация', async () => {
  const { getCachedTaskDetails, setCachedTaskDetails, invalidateTaskDetailsCache } = await import(
    '../src/lib/taskDetailsCache.ts'
  );

  const mockDetails = { id: 888, task: { id: 888 }, comments: [] } as any;
  setCachedTaskDetails(888, mockDetails);

  const cached = getCachedTaskDetails(888);
  assert.deepStrictEqual(cached, mockDetails);

  invalidateTaskDetailsCache(888);
  assert.strictEqual(getCachedTaskDetails(888), null);
});

test('commentsUtils: formatCommentsCount склоняет числительные корректно', async () => {
  const { formatCommentsCount } = await import(
    '../src/components/inspector/commentsUtils.ts'
  );

  assert.strictEqual(formatCommentsCount(0), '0 комментариев');
  assert.strictEqual(formatCommentsCount(1), '1 комментарий');
  assert.strictEqual(formatCommentsCount(2), '2 комментария');
  assert.strictEqual(formatCommentsCount(4), '4 комментария');
  assert.strictEqual(formatCommentsCount(5), '5 комментариев');
  assert.strictEqual(formatCommentsCount(11), '11 комментариев');
  assert.strictEqual(formatCommentsCount(14), '14 комментариев');
  assert.strictEqual(formatCommentsCount(21), '21 комментарий');
  assert.strictEqual(formatCommentsCount(22), '22 комментария');
  assert.strictEqual(formatCommentsCount(25), '25 комментариев');
  assert.strictEqual(formatCommentsCount(101), '101 комментарий');
});

test('commentsUtils: системные события TaskLifetimes без текста комментария отсекаются', async () => {
  const { getCommentText, filterMeaningfulComments, formatCommentsCount } = await import(
    '../src/components/inspector/commentsUtils.ts'
  );

  // Системное создание заявки IntraService
  const creationEvent = {
    Id: 101,
    TaskId: 555,
    StatusId: 31,
    Comments: null,
    Description: 'Создание заявки',
  };
  assert.strictEqual(getCommentText(creationEvent), '');

  // Системная смена статуса без ввода комментария
  const statusChange = {
    Id: 102,
    StatusId: 32,
    Comments: '',
    Description: "Статус изменен с 'Новая' на 'В работе'",
  };
  assert.strictEqual(getCommentText(statusChange), '');

  // Системное назначение исполнителя
  const assignEvent = {
    Id: 103,
    Comments: null,
    Description: 'Назначен исполнитель: Беликов Ален',
  };
  assert.strictEqual(getCommentText(assignEvent), '');

  // Системное прикрепление файла
  const fileEvent = {
    Id: 104,
    Comments: null,
    Description: 'Добавлен файл: report.pdf',
  };
  assert.strictEqual(getCommentText(fileEvent), '');

  // Заявка только что создана (в TaskLifetimes ровно 1 запись создания):
  const rawHistoryOnlyCreation = [creationEvent];
  const filtered = filterMeaningfulComments(rawHistoryOnlyCreation);
  assert.strictEqual(filtered.length, 0);
  assert.strictEqual(formatCommentsCount(filtered.length), '0 комментариев');
});

test('commentsUtils: содержательные комментарии пользователей и инженеров сохраняются', async () => {
  const { getCommentText, filterMeaningfulComments, formatCommentsCount } = await import(
    '../src/components/inspector/commentsUtils.ts'
  );

  // Обычный комментарий пользователя в поле Comments
  const userComment = {
    Id: 201,
    UserName: 'Смирнова Е.',
    Comments: 'Добрый день, принтер снова жует бумагу в лотке 2',
    Description: '',
  };
  assert.strictEqual(
    getCommentText(userComment),
    'Добрый день, принтер снова жует бумагу в лотке 2'
  );

  // Смена статуса со служебным комментарием инженера
  const engineerResolution = {
    Id: 202,
    UserName: 'Беликов Ален',
    Comments: 'Ролик захвата бумаги очищен, тест печати успешен.',
    Description: "Статус изменен на 'Выполнена'",
  };
  assert.strictEqual(
    getCommentText(engineerResolution),
    'Ролик захвата бумаги очищен, тест печати успешен.'
  );

  // Комментарий с HTML-разметкой очищается от тегов
  const htmlComment = {
    Id: 203,
    Comments: '<p>Первая строка<br/>Вторая строка&nbsp;сообщения</p>',
  };
  assert.strictEqual(
    getCommentText(htmlComment),
    'Первая строка\nВторая строка сообщения'
  );

  // Пустой HTML-комментарий отсекается
  const emptyHtml = {
    Id: 204,
    Comments: '<p>&nbsp;<br></p>',
  };
  assert.strictEqual(getCommentText(emptyHtml), '');

  // Смешанная история: 1 событие создания + 2 реальных комментария + 1 событие статуса
  const mixedHistory = [
    { Id: 1, Description: 'Создание заявки', Comments: null },
    userComment,
    { Id: 2, Description: "Статус изменен на 'В работе'", Comments: '' },
    engineerResolution,
  ];
  const filtered = filterMeaningfulComments(mixedHistory);
  assert.strictEqual(filtered.length, 2);
  assert.strictEqual(formatCommentsCount(filtered.length), '2 комментария');
});

test('safeClearText: защита от случайного стирания длинного текста', () => {
  function computeClearAction(currentText: string, isConfirmed: boolean): { newText: string; needsConfirm: boolean } {
    if (currentText.trim().length <= 20) {
      return { newText: '', needsConfirm: false };
    }
    if (!isConfirmed) {
      return { newText: currentText, needsConfirm: true };
    }
    return { newText: '', needsConfirm: false };
  }

  // Короткий текст стирается сразу без подтверждения
  const shortResult = computeClearAction('Короткий текст', false);
  assert.strictEqual(shortResult.newText, '');
  assert.strictEqual(shortResult.needsConfirm, false);

  // Длинный текст не стирается по первому клику, а требует подтверждения
  const longText = 'Здравствуйте! Проблема была исследована, выполнен сброс службы Spooler.';
  const firstClick = computeClearAction(longText, false);
  assert.strictEqual(firstClick.newText, longText);
  assert.strictEqual(firstClick.needsConfirm, true);

  // По повторному клику с подтверждением текст очищается
  const secondClick = computeClearAction(longText, true);
  assert.strictEqual(secondClick.newText, '');
  assert.strictEqual(secondClick.needsConfirm, false);
});

test('queueFiltering: фильтрация по сценариям и статусам IntraService', () => {
  const sampleTickets = [
    { id: '1', scenarioKey: 'install_printer', statusName: 'Открыта', statusId: 31, isProcessed: true },
    { id: '2', scenarioKey: 'install_printer', statusName: 'В работе', statusId: 27, isProcessed: true },
    { id: '3', scenarioKey: 'grant_wlan', statusName: 'Выполнена', statusId: 29, isProcessed: true },
    { id: '4', scenarioKey: undefined, statusName: 'Отменена', statusId: 30, isProcessed: true },
    { id: '5', scenarioKey: undefined, statusName: 'Открыта', statusId: 31, isProcessed: false },
  ];

  function filterQueue(
    tickets: typeof sampleTickets,
    processedTab: 'processed' | 'unprocessed',
    selectedScenario: string | null,
    selectedStatus: string | null,
  ) {
    const tabTickets = processedTab === 'processed'
      ? tickets.filter(t => t.isProcessed)
      : tickets.filter(t => !t.isProcessed);

    return tabTickets.filter(t => {
      // Фильтр сценария действует только на вкладке processed
      if (processedTab === 'processed' && selectedScenario !== null) {
        if (selectedScenario === '__none__') {
          if (t.scenarioKey) return false;
        } else if (t.scenarioKey !== selectedScenario) {
          return false;
        }
      }
      // Фильтр статусов действует регистронезависимо
      if (selectedStatus !== null) {
        if (t.statusName.trim().toLowerCase() !== selectedStatus.trim().toLowerCase()) {
          return false;
        }
      }
      return true;
    });
  }

  // 1. По умолчанию («Все сценарии», «Все статусы») — видны все проанализированные, включая Выполнена и Отменена!
  const defaultProcessed = filterQueue(sampleTickets, 'processed', null, null);
  assert.strictEqual(defaultProcessed.length, 4);
  assert.deepStrictEqual(defaultProcessed.map(t => t.id), ['1', '2', '3', '4']);

  // 2. Фильтр по сценарию install_printer
  const printerOnly = filterQueue(sampleTickets, 'processed', 'install_printer', null);
  assert.strictEqual(printerOnly.length, 2);
  assert.deepStrictEqual(printerOnly.map(t => t.id), ['1', '2']);

  // 3. Фильтр по сценарию install_printer + статус «В работе» (регистронезависимо)
  const printerInProgress = filterQueue(sampleTickets, 'processed', 'install_printer', 'в работе');
  assert.strictEqual(printerInProgress.length, 1);
  assert.strictEqual(printerInProgress[0].id, '2');

  // 4. Группа без сценария (__none__)
  const noScenario = filterQueue(sampleTickets, 'processed', '__none__', null);
  assert.strictEqual(noScenario.length, 1);
  assert.strictEqual(noScenario[0].id, '4');

  // 5. Переключение на вкладку unprocessed — фильтр сценария не блокирует выдачу
  const unprocessedWithScenario = filterQueue(sampleTickets, 'unprocessed', 'install_printer', null);
  assert.strictEqual(unprocessedWithScenario.length, 1);
  assert.strictEqual(unprocessedWithScenario[0].id, '5');

  // 6. Выполненные и отмененные заявки фильтруются корректно по статусу
  const resolvedOnly = filterQueue(sampleTickets, 'processed', null, 'Выполнена');
  assert.strictEqual(resolvedOnly.length, 1);
  assert.strictEqual(resolvedOnly[0].id, '3');
});

test('statusOptionsDictionary: полный справочник статусов со счетчиками и каноническим набором', () => {
  const CANONICAL_STATUS_LIST = [
    'Открыта',
    'В работе',
    'Требует уточнения',
    'Ждем пользователя',
    'Ждем поставку',
    'Плановые работы',
    'Обработано 1-й линией',
    'Выполнена',
    'Отменена',
    'Закрыта',
  ];

  const availableStatusesFromApi = [
    { id: 31, name: 'Открыта' },
    { id: 27, name: 'В работе' },
    { id: 99, name: 'На согласовании у руководства' }, // Кастомный статус
  ];

  const currentTabTickets = [
    { statusName: 'Открыта', statusId: 31 },
    { statusName: 'Открыта', statusId: 31 },
    { statusName: 'В работе', statusId: 27 },
  ];

  function buildStatusOptions(
    available: Array<{ id: number; name: string }>,
    tickets: Array<{ statusName: string; statusId?: number }>
  ) {
    const map = new Map<string, { name: string; count: number; statusId?: number }>();

    for (const st of available) {
      if (st.name) {
        const normKey = st.name.trim().toLowerCase();
        if (!map.has(normKey)) {
          map.set(normKey, { name: st.name.trim(), count: 0, statusId: st.id });
        }
      }
    }

    for (const name of CANONICAL_STATUS_LIST) {
      const normKey = name.toLowerCase();
      if (!map.has(normKey)) {
        map.set(normKey, { name, count: 0 });
      }
    }

    for (const t of tickets) {
      const rawName = (t.statusName || 'Открыта').trim();
      const normKey = rawName.toLowerCase();
      const existing = map.get(normKey);
      if (existing) {
        existing.count++;
      } else {
        map.set(normKey, { name: rawName, count: 1, statusId: t.statusId });
      }
    }

    return Array.from(map.values()).sort((a, b) => {
      if (a.count !== b.count) {
        return b.count - a.count;
      }
      return a.name.localeCompare(b.name, 'ru');
    });
  }

  const options = buildStatusOptions(availableStatusesFromApi, currentTabTickets);

  // 1. Первыми идут статусы с ненулевыми счетчиками по убыванию
  assert.strictEqual(options[0].name, 'Открыта');
  assert.strictEqual(options[0].count, 2);
  assert.strictEqual(options[1].name, 'В работе');
  assert.strictEqual(options[1].count, 1);

  // 2. Все канонические статусы присутствуют со счетчиком 0, а не пропадают из списка
  const zeroStatuses = options.filter(o => o.count === 0);
  assert.ok(zeroStatuses.some(o => o.name === 'Требует уточнения'));
  assert.ok(zeroStatuses.some(o => o.name === 'Выполнена'));
  assert.ok(zeroStatuses.some(o => o.name === 'Отменена'));
  assert.ok(zeroStatuses.some(o => o.name === 'Закрыта'));

  // 3. Кастомный статус из API также включен
  assert.ok(zeroStatuses.some(o => o.name === 'На согласовании у руководства'));

  // 4. Общее количество опций включает все канонические + кастомный
  assert.strictEqual(options.length, 11);
});

test('closedTicketsExcludedFromBatchAnalysis: закрытые заявки не берутся в пакетный анализ очереди', () => {
  const isClosedTicket = (ticket: { statusId?: number; status?: string }): boolean => {
    if (ticket.statusId && (ticket.statusId === 28 || ticket.statusId === 29 || ticket.statusId === 30)) {
      return true;
    }
    if (ticket.status === 'resolved') {
      return true;
    }
    return false;
  };

  // Проверка статусов: 28 (Закрыта), 29 (Выполнена), 30 (Отменена), resolved
  assert.strictEqual(isClosedTicket({ statusId: 31, status: 'new' }), false);
  assert.strictEqual(isClosedTicket({ statusId: 27, status: 'in_progress' }), false);
  assert.strictEqual(isClosedTicket({ statusId: 35, status: 'waiting' }), false);
  assert.strictEqual(isClosedTicket({ statusId: 29, status: 'resolved' }), true);
  assert.strictEqual(isClosedTicket({ statusId: 30, status: 'resolved' }), true);
  assert.strictEqual(isClosedTicket({ statusId: 28 }), true);

  // Очередь не проанализированных тикетов, где есть как открытые, так и завершенные
  const unprocessedTickets = [
    { rawId: 101, statusId: 31, status: 'new' },
    { rawId: 102, statusId: 27, status: 'in_progress' },
    { rawId: 103, statusId: 29, status: 'resolved' }, // Выполнена
    { rawId: 104, statusId: 30, status: 'resolved' }, // Отменена
    { rawId: 105, statusId: 28, status: 'resolved' }, // Закрыта
  ];

  // В пакетный запуск берутся только открытые
  const openTargets = unprocessedTickets
    .filter(t => !isClosedTicket(t))
    .map(t => t.rawId);

  assert.deepStrictEqual(openTargets, [101, 102]);
  assert.strictEqual(openTargets.includes(103), false);
  assert.strictEqual(openTargets.includes(104), false);
  assert.strictEqual(openTargets.includes(105), false);
});




