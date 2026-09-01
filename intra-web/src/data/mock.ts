export type Status = 'new' | 'in_progress' | 'waiting' | 'resolved';
export type Priority = 'critical' | 'high' | 'medium' | 'low';
export type Category = 'network' | 'hardware' | 'software' | 'access' | 'email';
export type Page = 'queue' | 'automation' | 'settings';

export interface Operator {
  id: string;
  name: string;
  initials: string;
}

export interface TimelineEvent {
  id: string;
  type: 'created' | 'reply' | 'internal' | 'status_change' | 'assignment';
  author: string;
  content: string;
  timestamp: Date;
}

export interface Ticket {
  id: string;
  title: string;
  status: Status;
  priority: Priority;
  category: Category;
  assigneeId: string | null;
  requesterName: string;
  requesterPhone: string;
  host: string;
  ip: string;
  slaDeadline: Date;
  createdAt: Date;
  description: string;
  aiConfidence: number | null;
  aiSuggestion: string | null;
  timeline: TimelineEvent[];
  ruleType?: string;
  templateKey?: string;
  targetServiceName?: string;
  isRedirect?: boolean;
  targetStatusId?: number;
  isDuplicate?: boolean;
  duplicateInfo?: any;
  attachments?: Array<{ id: number; name: string; size?: number; url?: string }>;
  expenses?: number;
}

export interface ToastMessage {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
}

export const operators: Operator[] = [
  { id: 'op1', name: 'Иванов А.В.', initials: 'ИА' },
  { id: 'op2', name: 'Петрова М.С.', initials: 'ПМ' },
  { id: 'op3', name: 'Сидоров Д.К.', initials: 'СД' },
  { id: 'op4', name: 'Козлова Н.Р.', initials: 'КН' },
];

const now = Date.now();
const h = (n: number) => new Date(now + n * 3600000);
const ago = (n: number) => new Date(now - n * 3600000);

export const mockTickets: Ticket[] = [
  {
    id: 'HD-1042',
    title: 'Нет доступа к сетевому диску Z:',
    status: 'new',
    priority: 'critical',
    category: 'access',
    assigneeId: null,
    requesterName: 'Антонова Е.В.',
    requesterPhone: '+7 (495) 123-45-01',
    host: 'DESKTOP-ACC012',
    ip: '192.168.1.45',
    slaDeadline: h(0.3),
    createdAt: ago(1.2),
    description: `## Описание проблемы\n\nПосле планового обновления сетевого оборудования в пятницу не могу подключиться к сетевому диску **Z:** (\\\\srv-file01\\buh).\n\nОшибка: *«Windows не может получить доступ к \\\\\\\\srv-file01\\\\buh»*\n\n### Шаги воспроизведения\n1. Открываю «Этот компьютер»\n2. Пытаюсь открыть диск Z:\n3. Получаю ошибку доступа\n\n### Дополнительно\n- Другие пользователи на этаже испытывают ту же проблему\n- Интернет работает нормально\n- Пинг до сервера проходит`,
    aiConfidence: 87,
    aiSuggestion: 'Проверить права доступа AD-группы на общей папке. Вероятно, учётная запись выпала из группы BUH_USERS после обновления GPO.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана через портал самообслуживания', timestamp: ago(1.2) },
      { id: 't2', type: 'status_change', author: 'Система', content: 'Статус изменён: Новая', timestamp: ago(1.2) },
    ],
  },
  {
    id: 'HD-1041',
    title: 'Зависает 1С:Бухгалтерия при открытии базы',
    status: 'in_progress',
    priority: 'high',
    category: 'software',
    assigneeId: 'op1',
    requesterName: 'Борисов П.К.',
    requesterPhone: '+7 (495) 123-45-02',
    host: 'DESKTOP-FIN023',
    ip: '192.168.1.78',
    slaDeadline: h(2.5),
    createdAt: ago(3),
    description: `## Проблема\n\n1С:Бухгалтерия 8.3 зависает на 30–60 секунд при открытии рабочей базы. После зависания работает нормально.\n\n**Версия:** 1С:Предприятие 8.3.22.1644\n**База:** PROD_BUH_2024\n\n\`\`\`\nОшибка COM: Соединение с сервером разорвано\nСервер: srv-1c01:1541\n\`\`\``,
    aiConfidence: 72,
    aiSuggestion: 'Очистить кэш 1С в папке %APPDATA%\\1C\\1cv8\\. Проверить нагрузку на srv-1c01.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана по телефону', timestamp: ago(3) },
      { id: 't2', type: 'assignment', author: 'Иванов А.В.', content: 'Заявка назначена на Иванов А.В.', timestamp: ago(2.5) },
      { id: 't3', type: 'status_change', author: 'Иванов А.В.', content: 'Статус изменён: В работе', timestamp: ago(2.5) },
      { id: 't4', type: 'internal', author: 'Иванов А.В.', content: 'Удалённо подключился, воспроизвёл проблему. Подозрение на кэш 1С или нагрузку на сервер. Проверяю.', timestamp: ago(2) },
    ],
  },
  {
    id: 'HD-1040',
    title: 'Принтер HP LaserJet 1320 не печатает',
    status: 'waiting',
    priority: 'medium',
    category: 'hardware',
    assigneeId: 'op2',
    requesterName: 'Волкова О.Н.',
    requesterPhone: '+7 (495) 123-45-03',
    host: 'DESKTOP-HR001',
    ip: '192.168.2.11',
    slaDeadline: h(6),
    createdAt: ago(5),
    description: `Принтер HP LaserJet 1320 перестал печатать. В очереди документы, статус принтера — «Готов», но ничего не выходит.\n\nПробовал перезагрузить — не помогло.`,
    aiConfidence: 91,
    aiSuggestion: 'Удалить и переустановить драйвер принтера. Очистить диспетчер печати: net stop spooler → удалить файлы из C:\\Windows\\System32\\spool\\PRINTERS → net start spooler.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана через портал', timestamp: ago(5) },
      { id: 't2', type: 'assignment', author: 'Петрова М.С.', content: 'Взята в работу', timestamp: ago(4.5) },
      { id: 't3', type: 'reply', author: 'Петрова М.С.', content: 'Добрый день! Для устранения проблемы нам потребуется временный доступ к вашему компьютеру. Будьте ли вы доступны сегодня с 15:00 до 16:00?', timestamp: ago(4) },
      { id: 't4', type: 'reply', author: 'Волкова О.Н.', content: 'Да, буду на месте. Жду.', timestamp: ago(3) },
      { id: 't5', type: 'status_change', author: 'Петрова М.С.', content: 'Статус изменён: Ожидание (ожидаем удобного времени для визита)', timestamp: ago(3) },
    ],
  },
  {
    id: 'HD-1039',
    title: 'VPN не подключается: ошибка 800',
    status: 'new',
    priority: 'high',
    category: 'network',
    assigneeId: null,
    requesterName: 'Григорьев М.А.',
    requesterPhone: '+7 (495) 123-45-04',
    host: 'LAPTOP-DEV007',
    ip: '10.20.1.33',
    slaDeadline: h(1),
    createdAt: ago(0.5),
    description: `С домашнего ноутбука не могу подключиться к корпоративному VPN.\n\nОшибка: **800 — невозможно установить VPN-подключение**\n\nРаньше работало, сломалось после обновления Windows 11 до 24H2.`,
    aiConfidence: 68,
    aiSuggestion: 'Проверить настройки VPN-клиента. После обновления Windows 11 24H2 изменились параметры PPTP. Возможно потребуется переключиться на L2TP/IPSec.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана по email', timestamp: ago(0.5) },
    ],
  },
  {
    id: 'HD-1038',
    title: 'Сброс пароля — учётная запись заблокирована',
    status: 'resolved',
    priority: 'medium',
    category: 'access',
    assigneeId: 'op3',
    requesterName: 'Дмитриева С.Ю.',
    requesterPhone: '+7 (495) 123-45-05',
    host: 'DESKTOP-MKT005',
    ip: '192.168.3.22',
    slaDeadline: h(12),
    createdAt: ago(8),
    description: `Учётная запись заблокирована после нескольких неверных попыток входа. Нужен сброс пароля.`,
    aiConfidence: 99,
    aiSuggestion: 'Стандартная процедура сброса пароля в AD. Разблокировать учётную запись, задать временный пароль, обязать смену при следующем входе.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана по звонку на горячую линию', timestamp: ago(8) },
      { id: 't2', type: 'assignment', author: 'Сидоров Д.К.', content: 'Взята в работу', timestamp: ago(7.5) },
      { id: 't3', type: 'reply', author: 'Сидоров Д.К.', content: 'Учётная запись разблокирована, временный пароль выслан на корпоративный email.', timestamp: ago(7) },
      { id: 't4', type: 'status_change', author: 'Сидоров Д.К.', content: 'Статус изменён: Решена', timestamp: ago(7) },
      { id: 't5', type: 'reply', author: 'Дмитриева С.Ю.', content: 'Спасибо, всё работает!', timestamp: ago(6.5) },
    ],
  },
  {
    id: 'HD-1037',
    title: 'Экран монитора периодически мигает',
    status: 'in_progress',
    priority: 'low',
    category: 'hardware',
    assigneeId: 'op4',
    requesterName: 'Ефимов К.Д.',
    requesterPhone: '+7 (495) 123-45-06',
    host: 'DESKTOP-DEV008',
    ip: '192.168.1.55',
    slaDeadline: h(20),
    createdAt: ago(10),
    description: `Монитор Dell U2722D мигает раз в несколько минут. Особенно заметно при работе в браузере.`,
    aiConfidence: 45,
    aiSuggestion: 'Возможно проблема с кабелем DisplayPort или драйвером видеокарты. Рекомендую замену кабеля.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана через портал', timestamp: ago(10) },
      { id: 't2', type: 'assignment', author: 'Козлова Н.Р.', content: 'Назначена на Козлова Н.Р.', timestamp: ago(9) },
      { id: 't3', type: 'internal', author: 'Козлова Н.Р.', content: 'Заменила кабель DisplayPort — проблема сохраняется. Попробую откатить драйвер видеокарты.', timestamp: ago(6) },
    ],
  },
  {
    id: 'HD-1036',
    title: 'Outlook не синхронизирует входящие',
    status: 'new',
    priority: 'high',
    category: 'email',
    assigneeId: null,
    requesterName: 'Захарова Т.Л.',
    requesterPhone: '+7 (495) 123-45-07',
    host: 'DESKTOP-MNG002',
    ip: '192.168.4.13',
    slaDeadline: h(1.5),
    createdAt: ago(0.3),
    description: `Outlook 2021 не синхронизирует входящие с Exchange. Последнее письмо получено вчера в 18:22, хотя на веб-версии есть новые сообщения.`,
    aiConfidence: 83,
    aiSuggestion: 'Проверить состояние профиля Outlook. Возможно требуется пересоздание OST-файла. Путь к OST: %LOCALAPPDATA%\\Microsoft\\Outlook\\',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана через портал', timestamp: ago(0.3) },
    ],
  },
  {
    id: 'HD-1035',
    title: 'Сервер SRV-FILE01 медленно отвечает',
    status: 'in_progress',
    priority: 'critical',
    category: 'network',
    assigneeId: 'op1',
    requesterName: 'Иванченко Р.В.',
    requesterPhone: '+7 (495) 123-45-08',
    host: 'SRV-FILE01',
    ip: '192.168.1.10',
    slaDeadline: h(-0.5),
    createdAt: ago(2),
    description: `Файловый сервер SRV-FILE01 отвечает с задержкой 3–5 секунд. Жалобы от всего отдела бухгалтерии (14 человек).`,
    aiConfidence: null,
    aiSuggestion: null,
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Создана автоматически системой мониторинга (alert: high latency)', timestamp: ago(2) },
      { id: 't2', type: 'assignment', author: 'Иванов А.В.', content: 'Взята в работу, эскалация 2-я линия', timestamp: ago(1.8) },
      { id: 't3', type: 'internal', author: 'Иванов А.В.', content: 'CPU 94%, RAM 98%. Запущен анализ процессов. Подозрение на индексацию поиска Windows.', timestamp: ago(1) },
    ],
  },
  {
    id: 'HD-1034',
    title: 'Не подключиться к RDP на DESKTOP-OPR031',
    status: 'waiting',
    priority: 'medium',
    category: 'network',
    assigneeId: 'op2',
    requesterName: 'Карпов Н.О.',
    requesterPhone: '+7 (495) 123-45-09',
    host: 'DESKTOP-OPR031',
    ip: '192.168.2.45',
    slaDeadline: h(4),
    createdAt: ago(6),
    description: `Не могу подключиться по RDP к рабочей станции из дома. Ошибка: «Не удаётся подключиться к удалённому компьютеру».`,
    aiConfidence: 76,
    aiSuggestion: 'Проверить, включён ли RDP на DESKTOP-OPR031. Возможно компьютер находится в режиме сна.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана по email', timestamp: ago(6) },
      { id: 't2', type: 'reply', author: 'Петрова М.С.', content: 'Для диагностики нам нужно, чтобы кто-то из офиса подошёл к DESKTOP-OPR031 и проверил, включён ли компьютер. Можете кого-то попросить?', timestamp: ago(4) },
      { id: 't3', type: 'status_change', author: 'Петрова М.С.', content: 'Статус изменён: Ожидание', timestamp: ago(4) },
    ],
  },
  {
    id: 'HD-1033',
    title: 'Excel падает при открытии файла .xlsm',
    status: 'resolved',
    priority: 'medium',
    category: 'software',
    assigneeId: 'op3',
    requesterName: 'Лебедева А.Г.',
    requesterPhone: '+7 (495) 123-45-10',
    host: 'DESKTOP-FIN008',
    ip: '192.168.1.88',
    slaDeadline: h(24),
    createdAt: ago(24),
    description: `Microsoft Excel 2021 вылетает при открытии файла отчёта.xlsm. Другие .xlsx файлы открываются нормально.`,
    aiConfidence: 95,
    aiSuggestion: 'Проблема с надстройкой или повреждённым VBA-проектом. Попробовать открыть в защищённом режиме: excel.exe /safe.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана', timestamp: ago(24) },
      { id: 't2', type: 'reply', author: 'Сидоров Д.К.', content: 'Исправлено: удалена надстройка Acrobat PDFMaker, конфликтовавшая с VBA. Excel работает стабильно.', timestamp: ago(20) },
      { id: 't3', type: 'status_change', author: 'Сидоров Д.К.', content: 'Статус: Решена', timestamp: ago(20) },
    ],
  },
  {
    id: 'HD-1032',
    title: 'Запрос прав администратора для установки ПО',
    status: 'new',
    priority: 'low',
    category: 'access',
    assigneeId: null,
    requesterName: 'Морозов Е.С.',
    requesterPhone: '+7 (495) 123-45-11',
    host: 'DESKTOP-DEV015',
    ip: '192.168.5.7',
    slaDeadline: h(48),
    createdAt: ago(0.8),
    description: `Прошу предоставить временные права локального администратора для установки Node.js и Docker Desktop.`,
    aiConfidence: 99,
    aiSuggestion: 'Стандартный запрос прав. Согласовать с руководителем отдела. Предоставить временный доступ сроком 1 день.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана через портал', timestamp: ago(0.8) },
    ],
  },
  {
    id: 'HD-1031',
    title: 'Не работает корпоративная почта на телефоне',
    status: 'new',
    priority: 'medium',
    category: 'email',
    assigneeId: null,
    requesterName: 'Никитина В.А.',
    requesterPhone: '+7 (495) 123-45-12',
    host: 'MOB-IOS-114',
    ip: '10.30.5.22',
    slaDeadline: h(3),
    createdAt: ago(1),
    description: `На iPhone корпоративная почта перестала приходить. Последний раз синхронизировалась позавчера.`,
    aiConfidence: 80,
    aiSuggestion: 'Проверить ActiveSync-профиль. Возможно требуется заново ввести пароль в настройках почты.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана по звонку', timestamp: ago(1) },
    ],
  },
  {
    id: 'HD-1030',
    title: 'Клавиатура и мышь зависают каждые несколько минут',
    status: 'in_progress',
    priority: 'medium',
    category: 'hardware',
    assigneeId: 'op4',
    requesterName: 'Орлова Д.М.',
    requesterPhone: '+7 (495) 123-45-13',
    host: 'DESKTOP-ACC005',
    ip: '192.168.1.77',
    slaDeadline: h(8),
    createdAt: ago(4),
    description: `Беспроводная клавиатура и мышь Logitech MK470 зависают на 2–3 секунды. Происходит примерно раз в 10 минут.`,
    aiConfidence: 61,
    aiSuggestion: 'Возможен конфликт USB или разряд батарей. Рекомендую заменить USB-ресивер на другой порт или заменить батареи.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана', timestamp: ago(4) },
      { id: 't2', type: 'internal', author: 'Козлова Н.Р.', content: 'Заменила батареи — без изменений. Переключила ресивер в другой USB-порт, наблюдаем.', timestamp: ago(2) },
    ],
  },
  {
    id: 'HD-1029',
    title: 'Настройка нового рабочего места для сотрудника',
    status: 'in_progress',
    priority: 'low',
    category: 'software',
    assigneeId: 'op2',
    requesterName: 'HR-Отдел',
    requesterPhone: '+7 (495) 123-45-14',
    host: 'DESKTOP-NEW-01',
    ip: '192.168.6.50',
    slaDeadline: h(24),
    createdAt: ago(8),
    description: `Новый сотрудник Павлов А.Е. выходит в понедельник. Нужно настроить рабочее место: установить ПО по стандарту компании, создать учётную запись AD, настроить почту.`,
    aiConfidence: null,
    aiSuggestion: null,
    timeline: [
      { id: 't1', type: 'created', author: 'Петрова М.С.', content: 'Создана по запросу HR-отдела', timestamp: ago(8) },
      { id: 't2', type: 'status_change', author: 'Петрова М.С.', content: 'В работе: создана учётная запись AD, настраивается рабочее место', timestamp: ago(4) },
    ],
  },
  {
    id: 'HD-1028',
    title: 'Сканер Canon DR-M160II не определяется',
    status: 'waiting',
    priority: 'low',
    category: 'hardware',
    assigneeId: 'op1',
    requesterName: 'Романова К.П.',
    requesterPhone: '+7 (495) 123-45-15',
    host: 'DESKTOP-REC003',
    ip: '192.168.2.88',
    slaDeadline: h(16),
    createdAt: ago(12),
    description: `После переустановки Windows сканер Canon DR-M160II не определяется. Пробовала скачать драйверы с сайта — не помогло.`,
    aiConfidence: 88,
    aiSuggestion: 'Установить драйвер CaptureOnTouch в режиме совместимости с Windows 10. Проверить USB 3.0 vs 2.0 порт.',
    timeline: [
      { id: 't1', type: 'created', author: 'Система', content: 'Заявка создана через портал', timestamp: ago(12) },
      { id: 't2', type: 'reply', author: 'Иванов А.В.', content: 'Попробуйте подключить сканер в USB 2.0 порт (синий разъём). Жду обратной связи.', timestamp: ago(8) },
      { id: 't3', type: 'status_change', author: 'Иванов А.В.', content: 'Ожидание ответа от заявителя', timestamp: ago(8) },
    ],
  },
];

export const savedFilters = [
  { id: 'sf1', name: 'Критичные — без исполнителя', icon: '🔴' },
  { id: 'sf2', name: 'Мои заявки сегодня', icon: '⭐' },
  { id: 'sf3', name: 'Нарушение SLA', icon: '⚠️' },
];

export const categoryLabel: Record<Category, string> = {
  network: 'Сеть',
  hardware: 'Железо',
  software: 'ПО',
  access: 'Доступ',
  email: 'Почта',
};

export const statusConfig: Record<Status, { label: string; className: string }> = {
  new: { label: 'Новая', className: 'bg-blue-50 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300' },
  in_progress: { label: 'В работе', className: 'bg-amber-50 text-amber-700 dark:bg-amber-950/60 dark:text-amber-300' },
  waiting: { label: 'Ожидание', className: 'bg-violet-50 text-violet-700 dark:bg-violet-950/60 dark:text-violet-300' },
  resolved: { label: 'Решена', className: 'bg-green-50 text-green-700 dark:bg-green-950/60 dark:text-green-300' },
};

export const priorityConfig: Record<Priority, { label: string; className: string; dotClass: string }> = {
  critical: { label: 'Критичный', className: 'bg-red-50 text-red-700 dark:bg-red-950/60 dark:text-red-300', dotClass: 'bg-red-500' },
  high: { label: 'Высокий', className: 'bg-orange-50 text-orange-700 dark:bg-orange-950/60 dark:text-orange-300', dotClass: 'bg-orange-500' },
  medium: { label: 'Средний', className: 'bg-yellow-50 text-yellow-700 dark:bg-yellow-950/60 dark:text-yellow-300', dotClass: 'bg-yellow-500' },
  low: { label: 'Низкий', className: 'bg-neutral-100 text-neutral-600 dark:bg-neutral-800/60 dark:text-neutral-400', dotClass: 'bg-neutral-400' },
};
