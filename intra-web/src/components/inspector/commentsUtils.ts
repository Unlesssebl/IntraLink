/**
 * Префиксы системных событий жизненного цикла IntraService (TaskLifetimes),
 * которые не являются сообщениями пользователей/инженеров.
 */
export const SYSTEM_EVENT_PREFIXES = [
  'Создание заявки',
  'Создана заявка',
  'Статус изменен',
  'Статус задачи изменен',
  'Назначен исполнитель',
  'Исполнитель изменен',
  'Изменен приоритет',
  'Приоритет изменен',
  'Добавлен файл',
  'Файл добавлен',
  'Удален файл',
  'Срок изменен',
  'Сервис изменен',
  'Услуга изменена',
  'Заявка закрыта',
  'Заявка переоткрыта',
  'Изменена категория',
  'Категория изменена',
  'Изменено описание',
  'Изменена тема',
  'Изменен заявитель',
  'Перенаправлена',
  'Автоматическое оповещение',
];

/**
 * Очищает HTML теги и спецсимволы, оставляя только содержательный текст.
 */
export function cleanCommentText(rawText: string): string {
  if (!rawText) return '';
  return rawText
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]*>/g, ' ')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n\s*\n+/g, '\n')
    .trim();
}

/**
 * Извлекает содержательный текст комментария из записи истории/жизненного цикла.
 * Если запись представляет собой системное событие без комментария, возвращает пустую строку ''.
 */
export function getCommentText(c: any): string {
  if (!c) return '';

  // 1. Приоритетные поля прямого комментария пользователя/инженера
  const rawDirect = c.text ?? c.Comments ?? c.Comment ?? c.Text;
  if (rawDirect != null) {
    const cleaned = cleanCommentText(String(rawDirect));
    if (cleaned) return cleaned;
  }

  // 2. Если поле комментария пустое, проверяем поле Description
  const rawDesc = c.Description;
  if (rawDesc != null) {
    const cleanedDesc = cleanCommentText(String(rawDesc));
    if (!cleanedDesc) return '';

    const lower = cleanedDesc.toLowerCase();
    const isSystemEvent = SYSTEM_EVENT_PREFIXES.some((prefix) =>
      lower.startsWith(prefix.toLowerCase())
    );

    if (isSystemEvent) {
      return '';
    }

    return cleanedDesc;
  }

  return '';
}

/**
 * Фильтрует список событий жизненного цикла, оставляя только содержательные человеческие комментарии.
 */
export function filterMeaningfulComments(comments: any[]): any[] {
  if (!Array.isArray(comments)) return [];
  return comments.filter((c) => Boolean(getCommentText(c)));
}

/**
 * Форматирует количество комментариев с учетом русской грамматики.
 */
export function formatCommentsCount(count: number): string {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${count} комментариев`;
  if (mod10 === 1) return `${count} комментарий`;
  if (mod10 >= 2 && mod10 <= 4) return `${count} комментария`;
  return `${count} комментариев`;
}
