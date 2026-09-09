/**
 * Единый канонический реестр и маппинг названий сценариев на русском языке.
 */

export interface ScenarioMeta {
  key: string;
  title: string;
  shortTitle: string;
  badgeClass: string;
  description?: string;
}

export const SCENARIO_REGISTRY: Record<string, ScenarioMeta> = {
  install_printer: {
    key: 'install_printer',
    title: 'Установка принтера / МФУ',
    shortTitle: 'Установка МФУ',
    badgeClass:
      'bg-purple-50/90 dark:bg-purple-950/40 text-purple-800 dark:text-purple-300 border-purple-200 dark:border-purple-900',
  },
  printer_installation: {
    key: 'printer_installation',
    title: 'Установка принтера / МФУ (legacy)',
    shortTitle: 'Установка МФУ',
    badgeClass:
      'bg-purple-50/90 dark:bg-purple-950/40 text-purple-800 dark:text-purple-300 border-purple-200 dark:border-purple-900',
  },
  printer_hardware_service: {
    key: 'printer_hardware_service',
    title: 'Сервисный ремонт оргтехники',
    shortTitle: 'Ремонт МФУ',
    badgeClass:
      'bg-amber-50/90 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900',
  },
  printer_scan_failure: {
    key: 'printer_scan_failure',
    title: 'Диагностика сетевого сканирования',
    shortTitle: 'Сбой сканирования',
    badgeClass:
      'bg-blue-50/90 dark:bg-blue-950/40 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-900',
  },
  printer_print_failure: {
    key: 'printer_print_failure',
    title: 'Устранение сбоя очереди печати',
    shortTitle: 'Сбой печати',
    badgeClass:
      'bg-rose-50/90 dark:bg-rose-950/40 text-rose-800 dark:text-rose-300 border-rose-200 dark:border-rose-900',
  },
  create_user: {
    key: 'create_user',
    title: 'Создание учётной записи (AD)',
    shortTitle: 'Создание УЗ',
    badgeClass:
      'bg-blue-50/90 dark:bg-blue-950/40 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-900',
  },
  user_creation: {
    key: 'user_creation',
    title: 'Создание учётной записи (AD legacy)',
    shortTitle: 'Создание УЗ',
    badgeClass:
      'bg-blue-50/90 dark:bg-blue-950/40 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-900',
  },
  grant_wlan: {
    key: 'grant_wlan',
    title: 'Доступ к корпоративному Wi-Fi',
    shortTitle: 'Wi-Fi доступ',
    badgeClass:
      'bg-emerald-50/90 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900',
  },
  wlan_access: {
    key: 'wlan_access',
    title: 'Доступ к корпоративному Wi-Fi (legacy)',
    shortTitle: 'Wi-Fi доступ',
    badgeClass:
      'bg-emerald-50/90 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900',
  },
  redirect: {
    key: 'redirect',
    title: 'Перенаправление в целевой сервис',
    shortTitle: 'Перенаправление',
    badgeClass:
      'bg-amber-50/90 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900',
  },
  service_redirect: {
    key: 'service_redirect',
    title: 'Перенаправление в целевой сервис (legacy)',
    shortTitle: 'Перенаправление',
    badgeClass:
      'bg-amber-50/90 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900',
  },
  offline_host: {
    key: 'offline_host',
    title: 'Диагностика недоступного ПК',
    shortTitle: 'ПК офлайн',
    badgeClass:
      'bg-rose-50/90 dark:bg-rose-950/40 text-rose-800 dark:text-rose-300 border-rose-200 dark:border-rose-900',
  },
  file_lock: {
    key: 'file_lock',
    title: 'Снятие блокировки файла (SMB)',
    shortTitle: 'Блокировка файла',
    badgeClass:
      'bg-amber-50/90 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900',
  },
  physical_device: {
    key: 'physical_device',
    title: 'Ремонт и перемещение оборудования',
    shortTitle: 'Каб. 112 (Ремонт)',
    badgeClass:
      'bg-indigo-50/90 dark:bg-indigo-950/40 text-indigo-800 dark:text-indigo-300 border-indigo-200 dark:border-indigo-900',
  },
  hardware_repair: {
    key: 'hardware_repair',
    title: 'Ремонт и перемещение оборудования (legacy)',
    shortTitle: 'Каб. 112 (Ремонт)',
    badgeClass:
      'bg-indigo-50/90 dark:bg-indigo-950/40 text-indigo-800 dark:text-indigo-300 border-indigo-200 dark:border-indigo-900',
  },
  rag_consultation: {
    key: 'rag_consultation',
    title: 'Консультация по базе знаний (RAG)',
    shortTitle: 'База знаний',
    badgeClass:
      'bg-cyan-50/90 dark:bg-cyan-950/40 text-cyan-800 dark:text-cyan-300 border-cyan-200 dark:border-cyan-900',
  },
  duplicate_task: {
    key: 'duplicate_task',
    title: 'Отмена заявки-дубликата',
    shortTitle: 'Дубликат',
    badgeClass:
      'bg-neutral-100 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border-neutral-300 dark:border-neutral-700',
  },
  duplicate: {
    key: 'duplicate',
    title: 'Отмена заявки-дубликата',
    shortTitle: 'Дубликат',
    badgeClass:
      'bg-neutral-100 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border-neutral-300 dark:border-neutral-700',
  },
  consultation: {
    key: 'consultation',
    title: 'Стандартная обработка 1-й линией',
    shortTitle: 'Консультация',
    badgeClass:
      'bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700',
  },
};

/**
 * Словарь для настроек автопилота и заголовков в UI
 */
export const SCENARIO_TITLES: Record<string, string> = Object.fromEntries(
  Object.entries(SCENARIO_REGISTRY).map(([k, v]) => [k, v.title])
);

/**
 * Словарь для кратких бейджей в очереди и таблицах
 */
export const SCENARIO_SHORT_TITLES: Record<string, string> = Object.fromEntries(
  Object.entries(SCENARIO_REGISTRY).map(([k, v]) => [k, v.shortTitle])
);

/**
 * Получить полное человекочитаемое название сценария
 */
export function getScenarioTitle(key?: string | null, version?: number | null): string {
  if (!key) return 'Решение первой линии';
  const meta = SCENARIO_REGISTRY[key];
  const title = meta ? meta.title : key.replace(/_/g, ' ');
  if (version && version > 1) {
    return `${title} (v${version})`;
  }
  return title;
}

/**
 * Получить краткое наименование сценария для бейджа
 */
export function getScenarioShortTitle(key?: string | null): string {
  if (!key) return '—';
  const meta = SCENARIO_REGISTRY[key];
  return meta ? meta.shortTitle : key;
}

/**
 * Получить CSS класс бейджа сценария
 */
export function getScenarioBadgeClass(key?: string | null): string {
  if (!key) {
    return 'bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700';
  }
  const meta = SCENARIO_REGISTRY[key];
  return (
    meta?.badgeClass ||
    'bg-blue-50 dark:bg-blue-950/50 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-900'
  );
}
