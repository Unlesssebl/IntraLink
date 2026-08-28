import { useState } from 'react';

interface Props {
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
}

type ServiceStatus = 'ok' | 'degraded' | 'down' | 'checking';

const StatusBadge = ({ status }: { status: ServiceStatus }) => {
  const cfg = {
    ok: { cls: 'bg-green-50 text-green-700 dark:bg-green-950/50 dark:text-green-300', label: 'Работает' },
    degraded: { cls: 'bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300', label: 'Деградация' },
    down: { cls: 'bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300', label: 'Недоступен' },
    checking: { cls: 'bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400', label: 'Проверка...' },
  }[status];
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded font-medium ${cfg.cls}`}>
      <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${
        status === 'ok' ? 'bg-green-500' : status === 'degraded' ? 'bg-amber-500' : status === 'down' ? 'bg-red-500' : 'bg-neutral-400'
      }`} />
      {cfg.label}
    </span>
  );
};

export default function SettingsPage({ theme, onToggleTheme }: Props) {
  const [services, setServices] = useState<{ id: string; name: string; type: string; status: ServiceStatus; latency?: string }[]>([
    { id: 's1', name: 'REST API Gateway', type: 'API', status: 'ok', latency: '12ms' },
    { id: 's2', name: 'Очередь сообщений (RabbitMQ)', type: 'Queue', status: 'ok', latency: '3ms' },
    { id: 's3', name: 'PostgreSQL', type: 'Database', status: 'ok', latency: '8ms' },
    { id: 's4', name: 'Telegram Bot', type: 'Bot', status: 'degraded', latency: '340ms' },
    { id: 's5', name: 'AI Engine (GPT-4o)', type: 'AI', status: 'ok', latency: '780ms' },
    { id: 's6', name: 'ActiveSync (Exchange)', type: 'Integration', status: 'down' },
  ]);

  const [density, setDensity] = useState<'comfortable' | 'compact' | 'spacious'>('comfortable');
  const [notifications, setNotifications] = useState(true);
  const [sound, setSound] = useState(false);
  const [visibleCols, setVisibleCols] = useState<Record<string, boolean>>({
    title: true, status: true, priority: true, ai: true,
    category: true, assignee: true, host: true, sla: true, requester: true,
  });

  const checkAll = () => {
    setServices(prev => prev.map(s => ({ ...s, status: 'checking' })));
    setTimeout(() => {
      setServices(prev => prev.map(s => ({
        ...s,
        status: s.id === 's6' ? 'down' : s.id === 's4' ? 'ok' : 'ok',
        latency: s.id === 's6' ? undefined : `${Math.floor(Math.random() * 100 + 5)}ms`,
      })));
    }, 1800);
  };

  return (
    <div className="h-full overflow-y-auto bg-neutral-50 dark:bg-neutral-950">
      <div className="max-w-3xl mx-auto px-6 py-6 space-y-5">
        <div>
          <h1 className="text-[17px] font-semibold text-neutral-900 dark:text-neutral-50 tracking-tight">Настройки</h1>
          <p className="text-[12px] text-neutral-500 dark:text-neutral-400 mt-0.5">Конфигурация IntraLink Helpdesk</p>
        </div>

        {/* Service status */}
        <section className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded">
          <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-100 dark:border-neutral-800">
            <div>
              <h2 className="text-[13px] font-semibold text-neutral-800 dark:text-neutral-200">Статус сервисов</h2>
              <p className="text-[11px] text-neutral-400 dark:text-neutral-600 mt-0.5">Мониторинг интеграций и компонентов</p>
            </div>
            <button
              onClick={checkAll}
              className="flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] text-neutral-600 dark:text-neutral-400 border border-neutral-200 dark:border-neutral-700 rounded hover:bg-neutral-50 dark:hover:bg-neutral-800 transition-colors"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M10 2a5 5 0 11-8.66 5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
                <path d="M10 2v3H7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
              Обновить
            </button>
          </div>
          <div className="divide-y divide-neutral-100 dark:divide-neutral-800">
            {services.map(svc => (
              <div key={svc.id} className="flex items-center gap-3 px-4 py-2.5">
                <div className="flex-1 min-w-0">
                  <p className="text-[13px] font-medium text-neutral-800 dark:text-neutral-200">{svc.name}</p>
                  <p className="text-[11px] text-neutral-400 dark:text-neutral-600 font-mono">{svc.type}</p>
                </div>
                {svc.latency && (
                  <span className="font-mono text-[11px] text-neutral-400 dark:text-neutral-600">{svc.latency}</span>
                )}
                <StatusBadge status={svc.status} />
              </div>
            ))}
          </div>
        </section>

        {/* Queue config */}
        <section className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded">
          <div className="px-4 py-3 border-b border-neutral-100 dark:border-neutral-800">
            <h2 className="text-[13px] font-semibold text-neutral-800 dark:text-neutral-200">Параметры очереди</h2>
            <p className="text-[11px] text-neutral-400 dark:text-neutral-600 mt-0.5">Только чтение — изменяется через конфигурацию инфраструктуры</p>
          </div>
          <div className="px-4 py-3 space-y-3">
            {[
              { label: 'Источник заявок', value: 'Telegram Bot + Email (Exchange) + Web Portal' },
              { label: 'Движок классификации', value: 'GPT-4o-mini (fine-tuned v2.1)' },
              { label: 'Векторная база', value: 'PostgreSQL pgvector (1536-dim)' },
              { label: 'Очередь сообщений', value: 'RabbitMQ 3.12 / exchange: helpdesk.tickets' },
              { label: 'Версия API', value: 'v2.4.1 / 2024-11-15' },
            ].map(row => (
              <div key={row.label} className="flex items-start gap-4">
                <span className="text-[12px] text-neutral-400 dark:text-neutral-600 w-44 shrink-0">{row.label}</span>
                <span className="text-[12px] font-mono text-neutral-700 dark:text-neutral-300">{row.value}</span>
              </div>
            ))}
          </div>
        </section>

        {/* Interface settings */}
        <section className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded">
          <div className="px-4 py-3 border-b border-neutral-100 dark:border-neutral-800">
            <h2 className="text-[13px] font-semibold text-neutral-800 dark:text-neutral-200">Интерфейс</h2>
          </div>
          <div className="px-4 py-3 space-y-4">
            {/* Theme */}
            <SettingRow label="Тема" desc="Светлая или тёмная цветовая схема">
              <button
                onClick={onToggleTheme}
                className="flex items-center gap-2 px-3 py-1.5 text-[12px] border border-neutral-200 dark:border-neutral-700 rounded hover:bg-neutral-50 dark:hover:bg-neutral-800 transition-colors text-neutral-700 dark:text-neutral-300"
              >
                {theme === 'light' ? (
                  <><svg width="12" height="12" viewBox="0 0 12 12" fill="none"><circle cx="6" cy="6" r="2.5" stroke="currentColor" strokeWidth="1.2"/><path d="M6 1v1.5M6 9.5V11M1 6h1.5M9.5 6H11M2.64 2.64l1.06 1.06M8.3 8.3l1.06 1.06M2.64 9.36l1.06-1.06M8.3 3.7l1.06-1.06" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg> Светлая</>
                ) : (
                  <><svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M10 7A4.5 4.5 0 015 2a4.5 4.5 0 100 9 4.5 4.5 0 005-4z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/></svg> Тёмная</>
                )}
              </button>
            </SettingRow>

            {/* Density */}
            <SettingRow label="Плотность таблицы" desc="Размер строк в очереди заявок">
              <div className="flex gap-1 bg-neutral-100 dark:bg-neutral-800 p-0.5 rounded">
                {(['compact', 'comfortable', 'spacious'] as const).map(d => (
                  <button
                    key={d}
                    onClick={() => setDensity(d)}
                    className={`px-2.5 py-1 rounded text-[11px] font-medium transition-colors ${
                      density === d ? 'bg-white dark:bg-neutral-700 text-neutral-900 dark:text-neutral-100 shadow-sm' : 'text-neutral-500 hover:text-neutral-700 dark:hover:text-neutral-300'
                    }`}
                  >
                    {d === 'compact' ? 'Компактный' : d === 'comfortable' ? 'Обычный' : 'Просторный'}
                  </button>
                ))}
              </div>
            </SettingRow>

            {/* Notifications */}
            <SettingRow label="Уведомления в браузере" desc="Push-уведомления о новых заявках">
              <Toggle value={notifications} onChange={setNotifications} />
            </SettingRow>
            <SettingRow label="Звуковые уведомления" desc="Звуковой сигнал при поступлении заявки">
              <Toggle value={sound} onChange={setSound} />
            </SettingRow>

            {/* Column visibility */}
            <div>
              <p className="text-[12px] font-medium text-neutral-700 dark:text-neutral-300 mb-1">Видимость колонок</p>
              <p className="text-[11px] text-neutral-400 dark:text-neutral-600 mb-2.5">Выберите колонки для отображения в таблице</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries({
                  title: 'Заявка', status: 'Статус', priority: 'Приоритет', ai: 'AI',
                  category: 'Категория', assignee: 'Исполнитель', host: 'Хост', sla: 'SLA', requester: 'Заявитель'
                }).map(([key, label]) => (
                  <button
                    key={key}
                    onClick={() => key !== 'title' && setVisibleCols(prev => ({ ...prev, [key]: !prev[key] }))}
                    disabled={key === 'title'}
                    className={`px-2.5 py-1 rounded text-[11px] font-medium border transition-colors ${
                      visibleCols[key]
                        ? 'bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 border-neutral-900 dark:border-neutral-100'
                        : 'bg-white dark:bg-neutral-900 text-neutral-500 dark:text-neutral-400 border-neutral-200 dark:border-neutral-700 hover:border-neutral-400'
                    } disabled:cursor-not-allowed`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function SettingRow({ label, desc, children }: { label: string; desc: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <p className="text-[12px] font-medium text-neutral-700 dark:text-neutral-300">{label}</p>
        <p className="text-[11px] text-neutral-400 dark:text-neutral-600">{desc}</p>
      </div>
      {children}
    </div>
  );
}

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!value)}
      className={`w-9 h-5 rounded-full transition-colors flex items-center ${value ? 'bg-blue-500' : 'bg-neutral-200 dark:bg-neutral-700'}`}
    >
      <div className={`w-3.5 h-3.5 bg-white rounded-full shadow-sm transition-transform ${value ? 'translate-x-4.5' : 'translate-x-0.5'}`} />
    </button>
  );
}
