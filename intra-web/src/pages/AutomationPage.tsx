import { useState, useEffect, useRef } from 'react';

interface ServiceItem {
  id: string;
  name: string;
  ragFill: number;
  enabled: boolean;
  children?: ServiceItem[];
}

const services: ServiceItem[] = [
  { id: 'net', name: 'Сетевое оборудование', ragFill: 78, enabled: true, children: [
    { id: 'net-vpn', name: 'VPN / удалённый доступ', ragFill: 92, enabled: true },
    { id: 'net-wifi', name: 'Wi-Fi / точки доступа', ragFill: 65, enabled: true },
    { id: 'net-switch', name: 'Коммутаторы / VLAN', ragFill: 44, enabled: false },
  ]},
  { id: 'soft', name: 'Программное обеспечение', ragFill: 85, enabled: true, children: [
    { id: 'soft-1c', name: '1С: Предприятие 8.3', ragFill: 96, enabled: true },
    { id: 'soft-ms', name: 'Microsoft Office 365', ragFill: 88, enabled: true },
    { id: 'soft-ac', name: 'Антивирус / EDR', ragFill: 71, enabled: true },
  ]},
  { id: 'hw', name: 'Аппаратное обеспечение', ragFill: 62, enabled: true, children: [
    { id: 'hw-print', name: 'Принтеры / МФУ', ragFill: 83, enabled: true },
    { id: 'hw-mon', name: 'Мониторы', ragFill: 55, enabled: false },
    { id: 'hw-kvm', name: 'Клавиатуры / мышки', ragFill: 40, enabled: true },
  ]},
  { id: 'acc', name: 'Управление доступом', ragFill: 97, enabled: true, children: [
    { id: 'acc-ad', name: 'Active Directory', ragFill: 99, enabled: true },
    { id: 'acc-mail', name: 'Корпоративная почта', ragFill: 94, enabled: true },
  ]},
];

const exampleQueries = [
  { q: 'Как сбросить пароль в Active Directory?', ai: 'Откройте «Active Directory — пользователи и компьютеры», найдите учётную запись, щёлкните правой кнопкой → «Сбросить пароль». Установите флаг «Сменить пароль при следующем входе». Временный пароль сообщите пользователю по телефону.', conf: 99 },
  { q: 'VPN не подключается ошибка 800', ai: 'Ошибка 800 означает, что клиент не может достичь VPN-сервера. Проверьте: 1) брандмауэр разрешает порт 1723 (PPTP) или 500/4500 (L2TP), 2) служба Routing and Remote Access запущена на сервере, 3) обновление Windows не сбросило настройки протокола.', conf: 74 },
  { q: 'Принтер не печатает документы из очереди', ai: 'Остановите и перезапустите службу диспетчера очереди: `net stop spooler && del /Q /F /S "%systemroot%\\System32\\spool\\PRINTERS\\*.*" && net start spooler`. После этого попробуйте печать снова.', conf: 91 },
];

const logLines = [
  { time: '14:32:01', level: 'INFO', msg: 'Triager started: processing 3 new tickets' },
  { time: '14:32:02', level: 'INFO', msg: 'Ticket HD-1042: category=access, confidence=87%' },
  { time: '14:32:02', level: 'SUCCESS', msg: 'Ticket HD-1042: AI suggestion generated, ready for review' },
  { time: '14:32:03', level: 'INFO', msg: 'Ticket HD-1039: category=network, confidence=68%' },
  { time: '14:32:03', level: 'WARN', msg: 'Ticket HD-1039: low confidence, flagged for manual review' },
  { time: '14:32:04', level: 'INFO', msg: 'Ticket HD-1036: category=email, confidence=83%' },
  { time: '14:32:04', level: 'SUCCESS', msg: 'Ticket HD-1036: AI suggestion generated' },
  { time: '14:32:05', level: 'INFO', msg: 'Triager complete: 2 ready, 1 flagged' },
  { time: '14:28:11', level: 'INFO', msg: 'KB sync started: service=Active Directory' },
  { time: '14:28:14', level: 'SUCCESS', msg: 'KB sync complete: 12 articles indexed, 2 updated' },
  { time: '14:15:02', level: 'ERROR', msg: 'KB sync failed: service=Коммутаторы/VLAN — insufficient examples (min 5 required)' },
  { time: '14:10:00', level: 'INFO', msg: 'Duplicate detection scan: 15 tickets analyzed' },
  { time: '14:10:01', level: 'WARN', msg: 'Possible duplicate detected: HD-1042 ~ HD-1028 (similarity 0.71)' },
];

const levelColor: Record<string, string> = {
  INFO: 'text-neutral-400',
  SUCCESS: 'text-green-400',
  WARN: 'text-amber-400',
  ERROR: 'text-red-400',
};

export default function AutomationPage() {
  const [selectedService, setSelectedService] = useState<ServiceItem | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['net', 'soft', 'hw', 'acc']));
  const [testQuery, setTestQuery] = useState('');
  const [testResult, setTestResult] = useState<{ answer: string; confidence: number } | null>(null);
  const [testing, setTesting] = useState(false);
  const [logFilter, setLogFilter] = useState<string>('ALL');
  const [triaging, setTriaging] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, []);

  const runTest = () => {
    if (!testQuery.trim()) return;
    setTesting(true);
    setTestResult(null);
    setTimeout(() => {
      const ex = exampleQueries.find(e => testQuery.toLowerCase().includes(e.q.split(' ')[0].toLowerCase()));
      setTestResult(ex
        ? { answer: ex.ai, confidence: ex.conf }
        : { answer: 'По данному запросу найдена похожая статья в базе знаний. Рекомендую обратиться к разделу документации или добавить примеры для повышения точности.', confidence: 52 }
      );
      setTesting(false);
    }, 1200);
  };

  const runTriage = () => {
    setTriaging(true);
    setTimeout(() => setTriaging(false), 2000);
  };

  const filteredLogs = logFilter === 'ALL' ? logLines : logLines.filter(l => l.level === logFilter);

  const toggleExpand = (id: string) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const renderService = (s: ServiceItem, depth = 0) => (
    <div key={s.id}>
      <button
        onClick={() => { setSelectedService(s); }}
        className={`w-full flex items-center gap-2 px-3 py-2 text-left transition-colors text-[12px] ${
          selectedService?.id === s.id
            ? 'bg-neutral-100 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100'
            : 'text-neutral-600 dark:text-neutral-400 hover:bg-neutral-50 dark:hover:bg-neutral-800/50 hover:text-neutral-900 dark:hover:text-neutral-100'
        }`}
        style={{ paddingLeft: `${12 + depth * 16}px` }}
      >
        {s.children && (
          <button
            onClick={e => { e.stopPropagation(); toggleExpand(s.id); }}
            className="shrink-0 text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300"
          >
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none" className={`transition-transform ${expanded.has(s.id) ? 'rotate-90' : ''}`}>
              <path d="M3 2l4 3-4 3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        )}
        {!s.children && <span className="w-2.5 shrink-0" />}
        <span className="flex-1 truncate">{s.name}</span>
        <div className="flex items-center gap-2 shrink-0">
          <div className="w-16 h-1 bg-neutral-200 dark:bg-neutral-700 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${s.ragFill >= 80 ? 'bg-green-500' : s.ragFill >= 50 ? 'bg-amber-500' : 'bg-red-400'}`}
              style={{ width: `${s.ragFill}%` }}
            />
          </div>
          <span className="text-[10px] font-mono text-neutral-400 w-6 text-right">{s.ragFill}%</span>
          <div
            onClick={e => { e.stopPropagation(); }}
            className={`w-7 h-3.5 rounded-full transition-colors cursor-pointer ${s.enabled ? 'bg-blue-500' : 'bg-neutral-300 dark:bg-neutral-700'}`}
          >
            <div className={`w-2.5 h-2.5 bg-white rounded-full mt-0.5 transition-transform ${s.enabled ? 'translate-x-4' : 'translate-x-0.5'}`} />
          </div>
        </div>
      </button>
      {s.children && expanded.has(s.id) && (
        <div>{s.children.map(c => renderService(c, depth + 1))}</div>
      )}
    </div>
  );

  return (
    <div className="h-full flex flex-col bg-neutral-50 dark:bg-neutral-950 overflow-hidden">
      {/* Stats bar */}
      <div className="shrink-0 flex items-center gap-0 border-b border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950">
        {[
          { label: 'Обработано сегодня', value: '47', icon: '✓', color: 'text-green-600 dark:text-green-400' },
          { label: 'Готовых ответов AI', value: '31', icon: '⭐', color: 'text-blue-600 dark:text-blue-400' },
          { label: 'Найдено дубликатов', value: '3', icon: '⊂', color: 'text-amber-600 dark:text-amber-400' },
        ].map((stat, i) => (
          <div key={i} className="flex-1 flex items-center gap-3 px-5 py-3.5 border-r border-neutral-100 dark:border-neutral-900 last:border-0">
            <div>
              <p className="text-[11px] text-neutral-500 dark:text-neutral-400">{stat.label}</p>
              <p className={`text-2xl font-semibold tracking-tight ${stat.color}`}>{stat.value}</p>
            </div>
          </div>
        ))}
        <div className="flex items-center gap-2 px-5">
          <button
            onClick={runTriage}
            disabled={triaging}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-[12px] font-medium rounded hover:bg-neutral-700 dark:hover:bg-neutral-300 disabled:opacity-50 transition-colors whitespace-nowrap"
          >
            {triaging ? (
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="animate-spin">
                <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.5" strokeOpacity="0.3"/>
                <path d="M6 1.5A4.5 4.5 0 0110.5 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
              </svg>
            ) : (
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M6 1.5L7.5 4.5H10.5L8 6.5L9 9.5L6 7.5L3 9.5L4 6.5L1.5 4.5H4.5L6 1.5Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round"/>
              </svg>
            )}
            {triaging ? 'Триаж...' : 'Запустить триаж'}
          </button>
          <button className="px-3 py-1.5 text-[12px] text-neutral-600 dark:text-neutral-400 border border-neutral-200 dark:border-neutral-700 rounded hover:bg-neutral-50 dark:hover:bg-neutral-800 transition-colors whitespace-nowrap">
            Синхр. базу знаний
          </button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Service catalog */}
        <div className="w-72 shrink-0 border-r border-neutral-200 dark:border-neutral-800 bg-white dark:bg-neutral-950 flex flex-col overflow-hidden">
          <div className="px-4 py-3 border-b border-neutral-100 dark:border-neutral-900">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">
              Каталог услуг
            </p>
          </div>
          <div className="flex-1 overflow-y-auto">
            {services.map(s => renderService(s))}
          </div>
        </div>

        {/* Detail panel */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {selectedService ? (
            <>
              <div>
                <h2 className="text-[15px] font-semibold text-neutral-900 dark:text-neutral-50">{selectedService.name}</h2>
                <p className="text-[12px] text-neutral-500 dark:text-neutral-400 mt-0.5">
                  Наполненность базы знаний: <span className="font-mono font-semibold">{selectedService.ragFill}%</span>
                </p>
              </div>

              {/* Settings */}
              <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-4 space-y-3">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600 mb-2">
                  Настройки
                </p>
                <div className="flex items-center justify-between">
                  <label className="text-[12px] text-neutral-700 dark:text-neutral-300">Порог уверенности AI</label>
                  <div className="flex items-center gap-2">
                    <input type="range" min={50} max={99} defaultValue={75} className="w-24 accent-blue-600" />
                    <span className="font-mono text-[12px] text-neutral-600 dark:text-neutral-400 w-8">75%</span>
                  </div>
                </div>
                <div className="flex items-center justify-between">
                  <label className="text-[12px] text-neutral-700 dark:text-neutral-300">Макс. примеров в ответе</label>
                  <input type="number" defaultValue={3} min={1} max={10} className="w-16 text-center text-[12px] font-mono bg-neutral-50 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded px-2 py-1 text-neutral-700 dark:text-neutral-300 outline-none" />
                </div>
                <div className="flex items-center justify-between">
                  <label className="text-[12px] text-neutral-700 dark:text-neutral-300">Автоназначение при уверенности {'>'} 90%</label>
                  <div className="w-9 h-5 bg-blue-500 rounded-full flex items-center cursor-pointer">
                    <div className="w-3.5 h-3.5 bg-white rounded-full ml-auto mr-0.5 shadow-sm" />
                  </div>
                </div>
              </div>

              {/* Knowledge base examples */}
              <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-4">
                <div className="flex items-center justify-between mb-3">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">
                    Примеры
                  </p>
                  <button className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline font-medium">
                    + Добавить
                  </button>
                </div>
                {exampleQueries.map((ex, i) => (
                  <div key={i} className="border-b border-neutral-100 dark:border-neutral-800 last:border-0 py-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-[12px] font-medium text-neutral-700 dark:text-neutral-300 flex-1">{ex.q}</p>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <span className={`font-mono text-[10px] px-1.5 py-0.5 rounded ${ex.conf >= 80 ? 'bg-green-50 text-green-700 dark:bg-green-950/50 dark:text-green-300' : 'bg-yellow-50 text-yellow-700 dark:bg-yellow-950/50 dark:text-yellow-300'}`}>
                          {ex.conf}%
                        </span>
                        <button className="text-neutral-300 hover:text-red-400 dark:text-neutral-700 dark:hover:text-red-400 transition-colors">
                          <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                            <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
                          </svg>
                        </button>
                      </div>
                    </div>
                    <p className="text-[11px] text-neutral-400 dark:text-neutral-600 mt-1 line-clamp-2">{ex.ai}</p>
                  </div>
                ))}
              </div>

              {/* Test playground */}
              <div className="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded p-4">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600 mb-3">
                  Тест-площадка
                </p>
                <textarea
                  value={testQuery}
                  onChange={e => setTestQuery(e.target.value)}
                  placeholder="Опишите проблему для тестирования AI-ответа..."
                  rows={2}
                  className="w-full px-3 py-2 text-[12px] bg-neutral-50 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded text-neutral-900 dark:text-neutral-100 placeholder-neutral-400 dark:placeholder-neutral-600 outline-none focus:ring-2 focus:ring-blue-500/30 focus:border-blue-500 transition-colors resize-none"
                />
                <button
                  onClick={runTest}
                  disabled={testing || !testQuery.trim()}
                  className="mt-2 px-3 py-1.5 text-[12px] font-medium bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 rounded hover:bg-neutral-700 dark:hover:bg-neutral-300 disabled:opacity-50 transition-colors"
                >
                  {testing ? 'Генерация...' : 'Тестировать'}
                </button>
                {testResult && (
                  <div className="mt-3 bg-neutral-50 dark:bg-neutral-800 rounded border border-neutral-200 dark:border-neutral-700 p-3">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-[11px] font-semibold text-neutral-500 dark:text-neutral-400">Ответ AI</span>
                      <span className={`font-mono text-[10px] px-1.5 py-0.5 rounded ${testResult.confidence >= 80 ? 'bg-green-50 text-green-700 dark:bg-green-950/50 dark:text-green-300' : 'bg-yellow-50 text-yellow-700 dark:bg-yellow-950/50 dark:text-yellow-300'}`}>
                        {testResult.confidence}%
                      </span>
                    </div>
                    <p className="text-[12px] text-neutral-700 dark:text-neutral-300 leading-relaxed">{testResult.answer}</p>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-neutral-400 dark:text-neutral-600">
              <svg width="40" height="40" viewBox="0 0 40 40" fill="none" className="mb-3 opacity-30">
                <path d="M20 4L23 13H32L25 18.5 27.5 28 20 23 12.5 28 15 18.5 8 13H17L20 4Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round"/>
              </svg>
              <p className="text-[13px]">Выберите раздел в каталоге услуг</p>
            </div>
          )}
        </div>

        {/* Agent event feed */}
        <div className="w-80 shrink-0 border-l border-neutral-200 dark:border-neutral-800 flex flex-col overflow-hidden bg-white dark:bg-neutral-950">
          <div className="px-4 py-3 border-b border-neutral-100 dark:border-neutral-900 flex items-center justify-between shrink-0">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-neutral-400 dark:text-neutral-600">Лента событий</p>
          </div>
          {/* Log console */}
          <div className="px-3 py-2 border-b border-neutral-100 dark:border-neutral-900 shrink-0 flex items-center gap-1.5">
            {['ALL', 'INFO', 'WARN', 'ERROR', 'SUCCESS'].map(f => (
              <button
                key={f}
                onClick={() => setLogFilter(f)}
                className={`text-[10px] font-mono px-1.5 py-0.5 rounded transition-colors ${
                  logFilter === f
                    ? 'bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900'
                    : 'text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300'
                }`}
              >
                {f}
              </button>
            ))}
          </div>
          <div
            ref={logRef}
            className="flex-1 overflow-y-auto bg-neutral-950 dark:bg-black p-3 font-mono"
          >
            {filteredLogs.map((line, i) => (
              <div key={i} className="flex gap-2 text-[11px] leading-5 mb-0.5">
                <span className="text-neutral-600 shrink-0">{line.time}</span>
                <span className={`shrink-0 w-14 ${levelColor[line.level]}`}>[{line.level}]</span>
                <span className="text-neutral-300 break-all">{line.msg}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
