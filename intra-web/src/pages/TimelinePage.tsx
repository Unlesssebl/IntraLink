import { useState, useEffect, useMemo, useCallback } from 'react';
import type { Page } from '../data/mock';

export type CategoryType = 'all' | 'core' | 'ai' | 'workers' | 'ui' | 'security';

export interface Milestone {
  id: string;
  version: string;
  date: string;
  title: string;
  category: CategoryType;
  categoryLabel: string;
  problem: string;
  solution: string;
  impact: string;
  modules: string[];
  commitRef: string;
  diagramType: 'pipeline' | 'broker' | 'rag' | 'security' | 'fsm' | 'orchestrator' | 'base';
}

export interface Epoch {
  id: number;
  roman: string;
  period: string;
  shortPeriod: string;
  title: string;
  subtitle: string;
  summary: string;
  accentColor: string;
  accentBorder: string;
  accentBg: string;
  accentGlow: string;
  stats: {
    commits: number;
    lines: string;
    keyMetric: string;
    goal: string;
  };
  milestones: Milestone[];
}

const EPOCHS: Epoch[] = [
  {
    id: 1,
    roman: '01',
    period: 'Март — Июнь 2026',
    shortPeriod: 'Март — Июнь',
    title: 'Рождение Ядра и Инфраструктурный Сдвиг',
    subtitle: 'От монолитного скрипта к распределенной асинхронной платформе',
    summary: 'Переход с локальных файлов на промышленный PostgreSQL, разделение монолита на FastAPI и бот, внедрение Redis шины и удаленной роботизации рабочих мест.',
    accentColor: '#38bdf8',
    accentBorder: 'border-sky-500/30',
    accentBg: 'bg-sky-500/10',
    accentGlow: 'rgba(56, 189, 248, 0.15)',
    stats: {
      commits: 113,
      lines: '36 000+',
      keyMetric: '0 SQLite блокировок',
      goal: 'Отказоустойчивый фундамент',
    },
    milestones: [
      {
        id: 'm-1-1',
        version: 'v1.0.0',
        date: '19.03.2026',
        title: 'Старт проекта и интеграционный фундамент',
        category: 'core',
        categoryLabel: 'Ядро и БД',
        problem: 'Все заявки обрабатывались дежурными инженерами вручную, медленный опрос приводил к пропускам аварийных инцидентов.',
        solution: 'Заложен фундамент монорепозитория, созданы первые коннекторы к API системы заявок и механизм фонового опроса событий.',
        impact: 'Мгновенное получение входящих инцидентов в автоматическом режиме 24/7.',
        modules: ['core-api/app/services/intraservice.py', 'core-api/app/config.py'],
        commitRef: '083cfb9',
        diagramType: 'base',
      },
      {
        id: 'm-1-2',
        version: 'v1.0.4',
        date: '04.06.2026',
        title: 'Контейнеризация и переход на Docker',
        category: 'core',
        categoryLabel: 'Инфраструктура',
        problem: 'Различия в окружениях разработчиков и серверов вызывали случайные ошибки импортов и версий библиотек.',
        solution: 'Упаковка всех компонентов в изолированные Docker-образы с multi-stage сборкой на базе Python 3.12/3.13.',
        impact: 'Развертывание системы одной командой, воспроизводимость стенда 100%.',
        modules: ['Dockerfile', 'docker-compose.yml', 'pyproject.toml'],
        commitRef: '8227125',
        diagramType: 'base',
      },
      {
        id: 'm-1-3',
        version: 'v1.1.0',
        date: '04.06.2026',
        title: 'Разделение монолита на Core API и Telegram-клиент',
        category: 'core',
        categoryLabel: 'Архитектура',
        problem: 'Сбои сетевых запросов в Telegram-боте намертво подвешивали весь процесс обработки тикетов.',
        solution: 'Разделение ответственности: асинхронный сервер FastAPI Core-API и отдельный независимый клиент уведомлений.',
        impact: 'Изоляция отказов: сбой клиентского бота больше не влияет на прием и анализ заявок.',
        modules: ['core-api/app/main.py', 'tg-bot/bot.py'],
        commitRef: '47fabf5',
        diagramType: 'broker',
      },
      {
        id: 'm-1-4',
        version: 'v1.2.0',
        date: '04.06.2026',
        title: 'Миграция на промышленную СУБД PostgreSQL',
        category: 'core',
        categoryLabel: 'База данных',
        problem: 'Файловая база SQLite блокировалась при одновременных запросах, грозя повреждением данных очереди.',
        solution: 'Полный отказ от SQLite; проектирование масштабируемой схемы в PostgreSQL с поддержкой параллельных блокировок.',
        impact: '100% защита данных, нулевые блокировки при параллельных сессиях нескольких операторов.',
        modules: ['core-api/app/db/session.py', 'alembic/versions/'],
        commitRef: 'a8e6abe',
        diagramType: 'base',
      },
      {
        id: 'm-1-5',
        version: 'v1.3.0',
        date: '04.06.2026',
        title: 'Асинхронная шина событий на базе Redis Pub/Sub',
        category: 'core',
        categoryLabel: 'Шина событий',
        problem: 'Синхронные вызовы сетевых проверок подвешивали пользовательские запросы операторов на 10-15 секунд.',
        solution: 'Внедрение шины очередей сообщений через Redis Pub/Sub для асинхронного оповещения о событиях тикетов.',
        impact: 'Мгновенный отклик интерфейса (<50 мс), фоновые задачи выполняются независимо от пользователя.',
        modules: ['core-api/app/services/redis_client.py'],
        commitRef: 'c838bfc',
        diagramType: 'broker',
      },
      {
        id: 'm-1-6',
        version: 'v1.3.5',
        date: '04.06.2026',
        title: 'Банковское шифрование учетных записей (Fernet)',
        category: 'security',
        categoryLabel: 'Безопасность',
        problem: 'Пароли и служебные токены инженеров хранились в открытом виде, создавая риск компрометации.',
        solution: 'Внедрение симметричного криптографического шифрования Fernet с мастер-ключами в защищенном окружении.',
        impact: 'Учетные записи защищены: даже при утечке базы прочесть пароли злоумышленникам невозможно.',
        modules: ['core-api/app/services/crypto.py'],
        commitRef: '92f6b9a',
        diagramType: 'security',
      },
      {
        id: 'm-1-7',
        version: 'v1.5.0',
        date: '09.06.2026',
        title: 'Роботизация рабочих станций без предварительной настройки',
        category: 'workers',
        categoryLabel: 'Роботы',
        problem: 'Для удаленной проверки ПК сотрудника требовалось вручную настраивать порты и удаленное управление.',
        solution: 'Разработка WMI Bootstrap — технологии автоматической дистанционной активации защищенного WinRM через системный RPC.',
        impact: 'Удаленный доступ к любому корпоративному компьютеру за 3 секунды без ручных манипуляций.',
        modules: ['printer-worker/wmi_bootstrap.py', 'printer-worker/action_executor.py'],
        commitRef: '88551a9',
        diagramType: 'orchestrator',
      },
      {
        id: 'm-1-8',
        version: 'v1.5.8',
        date: '11.06.2026',
        title: 'Умное автоматическое управление принтерами',
        category: 'workers',
        categoryLabel: 'Роботы',
        problem: 'До 30% обращений на 1-й линии составляли типовые проблемы с подключением принтеров и поиском драйверов.',
        solution: 'Автоматический опрос сети, сопоставление модели устройства со складом драйверов и фоновая установка на ПК.',
        impact: 'Сокращение времени подключения принтера с 25 минут ручной возни до 40 секунд автоматики.',
        modules: ['printer-worker/printer_installer.py', 'printer-worker/driver_indexer.py'],
        commitRef: '7a40133',
        diagramType: 'orchestrator',
      },
    ],
  },
  {
    id: 2,
    roman: '02',
    period: 'Июнь — Август 2026',
    shortPeriod: 'Июнь — Авг',
    title: 'Векторный Поиск и AI-Интеллект',
    subtitle: 'Переход от поиска по словам к семантическому пониманию сути проблем',
    summary: 'Интеграция векторной базы pgvector, локальных моделей FastEmbed, двухуровневого поиска BGE Reranker и автоматического самообучения на решениях инженеров.',
    accentColor: '#a855f7',
    accentBorder: 'border-purple-500/30',
    accentBg: 'bg-purple-500/10',
    accentGlow: 'rgba(168, 85, 247, 0.15)',
    stats: {
      commits: 27,
      lines: '18 000+',
      keyMetric: '94.6% точность AI',
      goal: 'Самообучающийся RAG',
    },
    milestones: [
      {
        id: 'm-2-1',
        version: 'v1.7.0',
        date: '17.06.2026',
        title: 'Векторная база знаний в PostgreSQL (pgvector)',
        category: 'ai',
        categoryLabel: 'AI и RAG',
        problem: 'Обычный поиск не находил решение, если пользователь описывал проблему другими словами («глючит 1С» вместо «ошибка платформы»).',
        solution: 'Внедрение расширения pgvector и преобразование описаний тикетов в многомерные семантические векторы.',
        impact: 'Система находит релевантные решения прошлых лет независимо от точных формулировок и опечаток.',
        modules: ['core-api/app/services/rag_service.py', 'alembic/versions/pgvector_init.py'],
        commitRef: '9b74918',
        diagramType: 'rag',
      },
      {
        id: 'm-2-2',
        version: 'v1.7.5',
        date: '18.06.2026',
        title: 'Автоматический AI-классификатор входящих заявок',
        category: 'ai',
        categoryLabel: 'AI и RAG',
        problem: 'Заявки падали в общую кучу, оператор тратил до 40% времени на первичное чтение и ручную рассылку.',
        solution: 'Нейросетевой классификатор анализирует текст и контекст, определяя точный сервис, приоритет и ответственного.',
        impact: 'Время первичной сортировки заявки сократилось с 15 минут до 1.5 секунды.',
        modules: ['core-api/app/services/ai_classifier.py'],
        commitRef: '01b43ec',
        diagramType: 'rag',
      },
      {
        id: 'm-2-3',
        version: 'v1.8.0',
        date: '19.08.2026',
        title: 'Параллельная экспресс-диагностика хостов заявителей',
        category: 'core',
        categoryLabel: 'Сетевой модуль',
        problem: 'Инженер тратил время на расспросы заявителя: включен ли компьютер, в сети ли он и работает ли связь.',
        solution: 'Автоматический опрос хоста (Ping, DNS, SMB:445, WinRM:5985) прямо в момент открытия карточки заявки.',
        impact: 'Оператор сразу видит: ПК включен, доступен и готов к удаленной настройке.',
        modules: ['core-api/app/services/network_diagnostics.py'],
        commitRef: '7b4a419',
        diagramType: 'orchestrator',
      },
      {
        id: 'm-2-4',
        version: 'v1.8.5',
        date: '19.08.2026',
        title: 'Самообучающаяся база решений в реальном времени',
        category: 'ai',
        categoryLabel: 'AI и RAG',
        problem: 'Статьи базы знаний устаревали за пару месяцев, потому что инженерам некогда вручную писать регламенты.',
        solution: 'Автоматический пайплайн: при закрытии заявки инженером система извлекает полезный опыт и вносит в векторную базу.',
        impact: 'База знаний непрерывно растет и обновляется автоматически без ручного написания статей.',
        modules: ['core-api/app/services/knowledge_sync.py'],
        commitRef: '9301d26',
        diagramType: 'rag',
      },
      {
        id: 'm-2-5',
        version: 'v1.9.3',
        date: '20.08.2026',
        title: 'Двухпроходный консенсус решений (Second-Pass)',
        category: 'ai',
        categoryLabel: 'AI и RAG',
        problem: 'Одиночная нейросеть могла галлюцинировать или предлагать шаблоны, не подходящие под корпоративные правила.',
        solution: 'Двухэтапная проверка: первая модель формирует черновик решения, а вторая строго валидирует его по критериям компании.',
        impact: 'Полное исключение нелепых ошибок и галлюцинаций в черновиках ответов заявителям.',
        modules: ['core-api/app/services/second_pass_verifier.py'],
        commitRef: '8b16803',
        diagramType: 'rag',
      },
      {
        id: 'm-2-6',
        version: 'v1.9.8',
        date: '21.08.2026',
        title: 'Отказоустойчивые потоки задач (Redis Streams)',
        category: 'core',
        categoryLabel: 'Шина событий',
        problem: 'При аварийной перезагрузке сервера во время тяжелой обработки задача могла бесследно потеряться из памяти.',
        solution: 'Миграция на надежные потоки Redis Streams с автоперехватом зависших задач другими воркерами (XAUTOCLAIM).',
        impact: 'Гарантия сохранности: ни одна заявка не теряется даже при аварийном отключении сервера.',
        modules: ['core-api/app/services/stream_broker.py'],
        commitRef: '210b808',
        diagramType: 'broker',
      },
      {
        id: 'm-2-7',
        version: 'v2.0.0',
        date: '21.08.2026',
        title: 'Рождение единой экосистемы IntraLink',
        category: 'core',
        categoryLabel: 'Архитектура',
        problem: 'Разрозненные скрипты и микросервисы имели несогласованные контракты и разные форматы сообщений.',
        solution: 'Официальный ребрендинг в IntraLink, стандартизация сверхбыстрой сериализации orjson и унификация репозитория.',
        impact: 'Единый надежный монорепозиторий с прозрачным взаимодействием всех компонентов.',
        modules: ['pyproject.toml', 'README.md', 'core-api/app/schemas/'],
        commitRef: '72e4ef9',
        diagramType: 'base',
      },
      {
        id: 'm-2-8',
        version: 'v2.0.5',
        date: '21.08.2026',
        title: 'Нейросетевой перепроверщик ответов (BGE Reranker)',
        category: 'ai',
        categoryLabel: 'AI и RAG',
        problem: 'Векторный поиск возвращал 20 похожих тикетов, но самое полезное решение могло оказаться внизу списка.',
        solution: 'Двухступенчатый поиск: векторная база отбирает топ-50 кандидатов, а глубокая нейросеть BGE точно ранжирует топ-3.',
        impact: 'Рекордная точность: 94.6% попаданий в идеальный ответ с первой попытки (рост с 71%).',
        modules: ['core-api/app/services/reranker.py'],
        commitRef: '7d037ce',
        diagramType: 'rag',
      },
      {
        id: 'm-2-9',
        version: 'v2.1.0',
        date: '21.08.2026',
        title: 'Автоматическое нахождение дубликатов и связывание',
        category: 'workers',
        categoryLabel: 'Роботы',
        problem: 'При общем сбое сети или 1С десятки сотрудников одновременно подавали одинаковые заявки, парализуя очередь.',
        solution: 'Семантический детектор в реальном времени связывает дубликаты в цепочки и предлагает массовое закрытие.',
        impact: 'Экономия до 2 часов времени дежурной смены при массовых офисных сбоях.',
        modules: ['core-api/app/services/duplicate_detector.py'],
        commitRef: '04f31ea',
        diagramType: 'pipeline',
      },
    ],
  },
  {
    id: 3,
    roman: '03',
    period: 'Конец Августа — 2 Сентября 2026',
    shortPeriod: 'Конец авг',
    title: 'Новый Веб-Интерфейс и Диспетчерский Пульт',
    subtitle: 'Рождение SPA intra-web, Zero-Emoji манифест и 100% контроль оператора',
    summary: 'Переход на современный React 19 SPA, отказ от эмодзи в пользу строгой темной темы Linear, внедрение диспетчерского триажа и DLP-защиты данных.',
    accentColor: '#34d399',
    accentBorder: 'border-emerald-500/30',
    accentBg: 'bg-emerald-500/10',
    accentGlow: 'rgba(52, 211, 153, 0.15)',
    stats: {
      commits: 50,
      lines: '24 000+',
      keyMetric: '100% HITL контроль',
      goal: 'Профессиональный диспетчерский пульт',
    },
    milestones: [
      {
        id: 'm-3-1',
        version: 'v2.2.0',
        date: '28.08.2026',
        title: 'Запуск современного веб-приложения Intra-Web',
        category: 'ui',
        categoryLabel: 'Рабочее место',
        problem: 'Старая админка требовала постоянных перезагрузок страниц и тормозила при переходе между заявками.',
        solution: 'Разработка быстрого Single-Page Application (SPA) на React 19, TypeScript и Vite со сборкой в один файл.',
        impact: 'Мгновенное открытие страниц (<100 мс), плавная работа без лагов на любых ПК.',
        modules: ['intra-web/src/App.tsx', 'intra-web/vite.config.ts'],
        commitRef: 'a1c63d9',
        diagramType: 'base',
      },
      {
        id: 'm-3-2',
        version: 'v2.2.4',
        date: '01.09.2026',
        title: 'Дизайн-манифест Zero-Emoji и стиль Linear Dark',
        category: 'ui',
        categoryLabel: 'Рабочее место',
        problem: 'Случайные эмодзи и кричащие цвета выглядели непрофессионально и утомляли глаза операторов за смену.',
        solution: 'Строгий регламент: отказ от эмодзи, переход на векторные SVG-иконки и глубокую графитовую тему Linear Dark.',
        impact: 'Премиальный строгий интерфейс, снижающий утомляемость зрения при 12-часовых сменах.',
        modules: ['intra-web/src/index.css', 'intra-web/src/components/Topbar.tsx'],
        commitRef: 'db7148e',
        diagramType: 'base',
      },
      {
        id: 'm-3-3',
        version: 'v2.3.0',
        date: '02.09.2026',
        title: 'Централизованная командная шина (Unified Command Bus)',
        category: 'core',
        categoryLabel: 'Архитектура',
        problem: 'Кнопки в интерфейсе вызывали разрозненные методы сервера, создавая хаос в логах и правах доступа.',
        solution: 'Все операции стандартизированы через шину команд: каждое действие — это строго типизированный контракт.',
        impact: 'Идеальная безопасность, детальное логирование и простота добавления новых сценариев.',
        modules: ['core-api/app/services/command_bus.py', 'core-api/app/routers/commands.py'],
        commitRef: 'bcf4b9d',
        diagramType: 'broker',
      },
      {
        id: 'm-3-4',
        version: 'v2.3.5',
        date: '02.09.2026',
        title: 'Диспетчерский пульт оператора 1-й линии',
        category: 'ui',
        categoryLabel: 'Рабочее место',
        problem: 'Оператор видел только список номеров, не понимая, какая заявка критична, а какая может подождать.',
        solution: 'Адаптивная таблица с цветовыми микро-дотами приоритетов, авто-определением автора и подсказками AI.',
        impact: 'Оператор за 2 секунды оценивает обстановку в очереди, не заходя внутрь каждого тикета.',
        modules: ['intra-web/src/pages/QueuePage.tsx', 'intra-web/src/components/TicketTable.tsx'],
        commitRef: '2e02e52',
        diagramType: 'pipeline',
      },
      {
        id: 'm-3-5',
        version: 'v2.4.0',
        date: '02.09.2026',
        title: 'Концепция «Человек в контуре» (Human-in-the-Loop)',
        category: 'ui',
        categoryLabel: 'Рабочее место',
        problem: 'Слепая авто-отправка ответов клиентам опасна, а полностью ручной ввод слишком медленный.',
        solution: 'Принцип HITL: система готовит точный ответ с процентом уверенности, а инженер утверждает его в 1 клик.',
        impact: 'Баланс скорости и безопасности: скорость робота при 100% гарантии контроля живым человеком.',
        modules: ['intra-web/src/components/inspector/UnifiedDecisionPanel.tsx'],
        commitRef: 'ee8ef11',
        diagramType: 'pipeline',
      },
      {
        id: 'm-3-6',
        version: 'v2.4.3',
        date: '02.09.2026',
        title: 'Смарт-пакетная обработка заявок (Batch Triage)',
        category: 'ui',
        categoryLabel: 'Рабочее место',
        problem: 'Утренний разбор пачки из 50 накопившихся заявок занимал у инженера более полутора часов.',
        solution: 'Пакетный режим: оператор отмечает чекбоксами подтвержденные советы AI и применяет их одной кнопкой.',
        impact: 'Разбор утренней стопки заявок сократился с 90 минут до 7 минут.',
        modules: ['intra-web/src/components/BatchActionBar.tsx', 'core-api/app/routers/triage.py'],
        commitRef: 'b45ff8e',
        diagramType: 'pipeline',
      },
      {
        id: 'm-3-7',
        version: 'v2.4.7',
        date: '02.09.2026',
        title: 'Очистка персональных данных перед отправкой в AI (DLP)',
        category: 'security',
        categoryLabel: 'Безопасность',
        problem: 'Передача текстов с паспортными данными, номерами карт и паролями во внешние LLM нарушает безопасность.',
        solution: 'Двухконтурный DLP-санитайзер: система автоматически маскирует чувствительные данные перед анализом нейросетью.',
        impact: 'Полное соответствие закону о персональных данных и корпоративной политике ИБ.',
        modules: ['core-api/app/services/pii_sanitizer.py'],
        commitRef: '0801cbc',
        diagramType: 'security',
      },
      {
        id: 'm-3-8',
        version: 'v2.5.0',
        date: '02.09.2026',
        title: 'Защитные блокировки от массовых сбоев (Safety Locks)',
        category: 'security',
        categoryLabel: 'Безопасность',
        problem: 'При аварии на шлюзе провайдера система могла начать массово закрывать тикеты как недоступные.',
        solution: 'Внедрение аварийного сторожа: при превышении порога аномалий автоматические действия замораживаются.',
        impact: 'Надежная страховка от лавинных сбоев автоматики при внешних форс-мажорах.',
        modules: ['core-api/app/services/safety_locks.py'],
        commitRef: '763df71',
        diagramType: 'security',
      },
    ],
  },
  {
    id: 4,
    roman: '04',
    period: '3 — 7 Сентября 2026',
    shortPeriod: '3–7 сен',
    title: 'Транзакционная Надежность и Неизменяемый Аудит',
    subtitle: 'Строгая стейт-машина жизненного цикла и прозрачный журнал решений',
    summary: 'Внедрение транзакционных команд v2 в базе данных, FSM-автомата жизненного цикла заявок, сквозного Decision Audit журнала и платформы воркеров v2.',
    accentColor: '#f59e0b',
    accentBorder: 'border-amber-500/30',
    accentBg: 'bg-amber-500/10',
    accentGlow: 'rgba(245, 158, 11, 0.15)',
    stats: {
      commits: 35,
      lines: '19 000+',
      keyMetric: '100% аудит решений',
      goal: 'Объяснимость и отказоустойчивость',
    },
    milestones: [
      {
        id: 'm-4-1',
        version: 'v2.6.0',
        date: '05.09.2026',
        title: 'Транзакционная платформа команд v2 (Alembic)',
        category: 'core',
        categoryLabel: 'Ядро и БД',
        problem: 'При разрыве соединения во время ответа было непонятно, выполнилась ли команда в базе или зависла.',
        solution: 'Неизменяемая таблица команд со статусами PENDING -> RUNNING -> COMPLETED и гарантией идемпотентности.',
        impact: 'Исключены повторные списания, дублирующиеся комментарии и потерянные действия.',
        modules: ['alembic/versions/20260905_commands_v2.py', 'core-api/app/services/command_executor.py'],
        commitRef: '0194291',
        diagramType: 'broker',
      },
      {
        id: 'm-4-2',
        version: 'v2.6.4',
        date: '05.09.2026',
        title: 'Строгая изоляция сессий операторов',
        category: 'security',
        categoryLabel: 'Безопасность',
        problem: 'При совместной работе операторов была вероятность взаимной перезаписи черновиков ответов.',
        solution: 'Криптографическая привязка сессий, проверка прав на каждое действие и изоляция черновиков.',
        impact: 'Исключены перекрестные ошибки и случайные перезаписи решений между инженерами.',
        modules: ['core-api/app/services/auth.py', 'intra-web/src/lib/auth.tsx'],
        commitRef: '69a395b',
        diagramType: 'security',
      },
      {
        id: 'm-4-3',
        version: 'v2.7.0',
        date: '06.09.2026',
        title: 'Умный автомат жизненного цикла тикетов (FSM)',
        category: 'core',
        categoryLabel: 'Архитектура',
        problem: 'Заявки могли попадать в тупиковые статусы (например, «закрыта», но назначен повторный опрос).',
        solution: 'Конечный автомат (FSM) с математически строгой матрицей разрешенных переходов между статусами.',
        impact: 'Нулевая вероятность зацикливания или зависания тикетов в некорректных состояниях.',
        modules: ['core-api/app/services/ticket_lifecycle.py'],
        commitRef: '66aa3cf',
        diagramType: 'fsm',
      },
      {
        id: 'm-4-4',
        version: 'v2.7.5',
        date: '06.09.2026',
        title: 'Неизменяемый электронный журнал аудита решений',
        category: 'ai',
        categoryLabel: 'AI и RAG',
        problem: 'Если автоматика отменяла дубликат или перенаправляла тикет, было трудно восстановить причину решения.',
        solution: 'Decision Audit Journal: система сохраняет цепочку фактов, сработавших правил и уверенность модели.',
        impact: '100% прозрачность: к любому действию приложено понятное обоснование для руководства и заявителя.',
        modules: ['core-api/app/services/decision_journal.py', 'core-api/app/models/decision_audit.py'],
        commitRef: '2ffe1ae',
        diagramType: 'rag',
      },
      {
        id: 'm-4-5',
        version: 'v2.8.0',
        date: '07.09.2026',
        title: 'Платформа независимых воркеров v2',
        category: 'workers',
        categoryLabel: 'Роботы',
        problem: 'Сбой одного специализированного воркера требовал ручного перезапуска администратором.',
        solution: 'Модульная платформа с контролем пульса (heartbeat) и мгновенным авто-перезапуском при любых сбоях.',
        impact: 'Непрерывная работа 24/7 без дежурства системных администраторов по ночам.',
        modules: ['core-api/app/services/worker_platform.py'],
        commitRef: 'cd3bb9d',
        diagramType: 'orchestrator',
      },
      {
        id: 'm-4-6',
        version: 'v2.8.4',
        date: '07.09.2026',
        title: 'Нейросетевой сенсор извлечения фактов из переписки',
        category: 'ai',
        categoryLabel: 'AI и RAG',
        problem: 'Заявители пишут важные детали (кабинет, модель МФУ, телефон) посреди длинного эмоционального текста.',
        solution: 'Сенсор фактов: автоматически извлекает ключевые параметры из текста и структурирует в карточке.',
        impact: 'Инженер не ищет кабинет и телефон глазами — они уже аккуратно вынесены в шапку заявки.',
        modules: ['core-api/app/services/fact_sensor.py'],
        commitRef: '1910a3b',
        diagramType: 'rag',
      },
      {
        id: 'm-4-7',
        version: 'v2.9.0',
        date: '07.09.2026',
        title: 'Мгновенные живые события без перезагрузки (SSE)',
        category: 'ui',
        categoryLabel: 'Рабочее место',
        problem: 'Чтобы увидеть новые заявки или смену статусов, диспетчеру приходилось постоянно обновлять страницу.',
        solution: 'Протокол Server-Sent Events (SSE): сервер мгновенно транслирует новые события прямо в открытый браузер.',
        impact: 'Мгновенная реакция: новые заявки и смены статусов появляются на экране за доли секунды.',
        modules: ['core-api/app/routers/events.py', 'intra-web/src/hooks/useLiveEvents.ts'],
        commitRef: '949a578',
        diagramType: 'pipeline',
      },
    ],
  },
  {
    id: 5,
    roman: '05',
    period: '8 Сентября 2026 — Настоящее время',
    shortPeriod: '8 сен (сейчас)',
    title: 'Автономный Автопилот и Умные Сценарии',
    subtitle: 'Переход к сквозным цепочкам сценариев и разделению очереди оператора',
    summary: 'Оркестратор TicketRun, автономное создание пользователей в Active Directory, безопасная канареечная выкатка и новый дизайн очереди с пайплайном шагов.',
    accentColor: '#f43f5e',
    accentBorder: 'border-rose-500/30',
    accentBg: 'bg-rose-500/10',
    accentGlow: 'rgba(244, 63, 94, 0.15)',
    stats: {
      commits: 40,
      lines: '14 000+',
      keyMetric: '0-Click автопилот',
      goal: 'Сквозные интеллектуальные сценарии',
    },
    milestones: [
      {
        id: 'm-5-1',
        version: 'v2.9.5',
        date: '08.09.2026',
        title: 'Автономное заведение учетных записей Active Directory',
        category: 'workers',
        categoryLabel: 'Роботы',
        problem: 'Ручное заведение нового сотрудника в домене через системные оснастки занимало до 20 минут на человека.',
        solution: 'Сквозной сценарий: проверка кадровых политик, генерация логина, включение в группы и создание в AD за 1 клик.',
        impact: 'Время регистрации сотрудника сократилось с 20 минут до 10 секунд с полным аудитом действий.',
        modules: ['core-api/app/services/ad_autopilot.py', 'core-api/app/services/action_policies.py'],
        commitRef: 'b9506ff',
        diagramType: 'orchestrator',
      },
      {
        id: 'm-5-2',
        version: 'v3.0.0',
        date: '08.09.2026',
        title: 'Большое слияние платформы и мониторинг аварий',
        category: 'core',
        categoryLabel: 'Архитектура',
        problem: 'Развитие разных веток монорепозитория требовало объединения в стабильный производственный релиз.',
        solution: 'Интеграция Worker Platform v2, Decision Audit и AIOps Outage SSE в главную ветку develop.',
        impact: 'Единая экосистема готова к максимальным корпоративным нагрузкам.',
        modules: ['git merge feat/decision-audit-ui-unification into develop'],
        commitRef: '949a578',
        diagramType: 'base',
      },
      {
        id: 'm-5-3',
        version: 'v3.0.5',
        date: '08.09.2026',
        title: 'Иерархическая навигация по каталогу услуг',
        category: 'ai',
        categoryLabel: 'AI и RAG',
        problem: 'Пользователи часто ошибались при выборе темы, путая 1С, электронную почту и доступы к папкам.',
        solution: 'Древовидная стратификация с квотами: сопоставление проблемы со структурой подразделений.',
        impact: 'Точная маршрутизация заявок даже в случаях, когда заявитель выбрал не тот раздел.',
        modules: ['core-api/app/services/catalog_stratification.py', 'core-api/app/routers/admin_kb.py'],
        commitRef: '092cade',
        diagramType: 'rag',
      },
      {
        id: 'm-5-4',
        version: 'v3.1.0',
        date: '08.09.2026',
        title: 'Переход к парадигме сквозных сценариев (Scenario Foundations)',
        category: 'core',
        categoryLabel: 'Архитектура',
        problem: 'Точечные правила не справлялись со сложными задачами, требующими цепочки последовательных шагов.',
        solution: 'Концепция TicketRun: заявка проходит по этапам сценария (Сбор данных -> Проверка -> Действие -> Контроль).',
        impact: 'Возможность полностью автоматизировать многоэтапные регламенты обслуживания.',
        modules: ['core-api/app/services/scenarios/base.py', 'core-api/app/services/ticket_runs.py'],
        commitRef: 'fabaf5c',
        diagramType: 'pipeline',
      },
      {
        id: 'm-5-5',
        version: 'v3.1.5',
        date: '08.09.2026',
        title: 'Оркестратор сценариев (TicketRunOrchestrator)',
        category: 'workers',
        categoryLabel: 'Роботы',
        problem: 'Требовался единый диспетчер для управления цепочками действий с интеллектуальной обработкой сбоев.',
        solution: 'Оркестратор сценариев: сохраняет факты и координирует переходы между шагами решения.',
        impact: 'Если компьютер выключен — сценарий запросит включение; если включен — продолжит установку ПО.',
        modules: ['core-api/app/services/ticket_run_orchestrator.py'],
        commitRef: '9b1d567',
        diagramType: 'orchestrator',
      },
      {
        id: 'm-5-6',
        version: 'v3.2.0',
        date: '08.09.2026',
        title: 'Безопасная плавная раскатка сценариев (Canary Rollout)',
        category: 'core',
        categoryLabel: 'Архитектура',
        problem: 'Опасно включать новый сложный сценарий сразу на 100% входящего потока заявок компании.',
        solution: 'Канареечный режим: сценарий сначала обкатывается на 10% тикетов в режиме теневого анализа (Shadow Mode).',
        impact: 'Нулевой риск: любые шероховатости сценария выявляются до массового включения.',
        modules: ['core-api/app/services/rollout_manager.py'],
        commitRef: '7a89150',
        diagramType: 'fsm',
      },
      {
        id: 'm-5-7',
        version: 'v3.2.5',
        date: '08.09.2026',
        title: 'Ручная корректировка фактов в интерфейсе (FactBag Override)',
        category: 'ui',
        categoryLabel: 'Рабочее место',
        problem: 'Если заявитель ошибся в номере своего кабинета, сценарий мог пойти по неверному пути.',
        solution: 'Инженер в карточке заявки может в 1 клик поправить любой параметр, и сценарий мгновенно адаптируется.',
        impact: 'Полная гибкость: оператор в любой момент контролирует и направляет логику автопилота.',
        modules: ['intra-web/src/components/inspector/UnifiedDecisionPanel.tsx'],
        commitRef: 'ca40b9b',
        diagramType: 'pipeline',
      },
      {
        id: 'm-5-8',
        version: 'v3.3.0',
        date: '08.09.2026',
        title: 'Редизайн очереди оператора: разделение потоков и Scenario Pipeline',
        category: 'ui',
        categoryLabel: 'Рабочее место',
        problem: 'Обработанные автоматикой и ждущие внимания заявки перемешивались в общей ленте.',
        solution: 'Разделение очереди на «В очереди» и «Обработано» с интерактивной шкалой шагов сценария прямо в строке.',
        impact: 'Идеальная наглядность: оператор сразу видит результаты автопилота и занимается только сложными тикетами.',
        modules: ['intra-web/src/pages/QueuePage.tsx', 'intra-web/src/components/QueueTabs.tsx'],
        commitRef: 'bf1baa5',
        diagramType: 'pipeline',
      },
    ],
  },
];

interface Props {
  onNavigate: (page: Page) => void;
}

export default function TimelinePage({ onNavigate }: Props) {
  const [activeEpochIndex, setActiveEpochIndex] = useState(4); // Default to current epoch 5
  const [viewMode, setViewMode] = useState<'deck' | 'feed'>('deck');
  const [selectedCategory, setSelectedCategory] = useState<CategoryType>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [activeMilestone, setActiveMilestone] = useState<Milestone | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [copiedRef, setCopiedRef] = useState<string | null>(null);

  const activeEpoch = EPOCHS[activeEpochIndex];

  // Global statistics
  const totalStats = useMemo(() => {
    return {
      lines: '111 000+',
      commits: 265,
      modules: 520,
      releases: '45+',
      accuracy: '94.6%',
      routineReduction: '-85%',
      latency: '< 1.2 с',
      zeroClick: '0-Click',
    };
  }, []);

  // Filtered milestones across all epochs for feed view
  const allMilestones = useMemo(() => {
    return EPOCHS.flatMap(epoch => epoch.milestones.map(m => ({ ...m, epochTitle: epoch.title, epochRoman: epoch.roman, epochAccent: epoch.accentColor })));
  }, []);

  const filteredMilestones = useMemo(() => {
    return allMilestones.filter(m => {
      const matchesCategory = selectedCategory === 'all' || m.category === selectedCategory;
      const query = searchQuery.toLowerCase().trim();
      const matchesSearch = !query || 
        m.title.toLowerCase().includes(query) ||
        m.version.toLowerCase().includes(query) ||
        m.solution.toLowerCase().includes(query) ||
        m.problem.toLowerCase().includes(query);
      return matchesCategory && matchesSearch;
    });
  }, [allMilestones, selectedCategory, searchQuery]);

  // Autoplay timer
  useEffect(() => {
    if (!isPlaying || viewMode !== 'deck') return;

    const interval = setInterval(() => {
      setProgress(prev => {
        if (prev >= 100) {
          setActiveEpochIndex(idx => (idx + 1) % EPOCHS.length);
          return 0;
        }
        return prev + 1;
      });
    }, 100); // 100 * 100ms = 10s per epoch

    return () => clearInterval(interval);
  }, [isPlaying, viewMode]);

  // Reset progress on epoch change
  useEffect(() => {
    setProgress(0);
  }, [activeEpochIndex]);

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      if (e.code === 'Space') {
        e.preventDefault();
        setIsPlaying(prev => !prev);
      } else if (e.code === 'ArrowRight') {
        e.preventDefault();
        setActiveEpochIndex(idx => Math.min(idx + 1, EPOCHS.length - 1));
      } else if (e.code === 'ArrowLeft') {
        e.preventDefault();
        setActiveEpochIndex(idx => Math.max(idx - 1, 0));
      } else if (e.key >= '1' && e.key <= '5') {
        e.preventDefault();
        setActiveEpochIndex(parseInt(e.key, 10) - 1);
      } else if (e.code === 'KeyT') {
        e.preventDefault();
        setViewMode(mode => (mode === 'deck' ? 'feed' : 'deck'));
      } else if (e.code === 'Escape') {
        setActiveMilestone(null);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const handleCopyRef = useCallback((ref: string) => {
    navigator.clipboard.writeText(ref);
    setCopiedRef(ref);
    setTimeout(() => setCopiedRef(null), 2000);
  }, []);

  return (
    <div className="h-screen w-screen flex flex-col bg-[#06090e] text-neutral-100 overflow-hidden select-none relative font-sans">
      {/* Background Ambient Mesh Glow */}
      <div 
        className="absolute inset-0 pointer-events-none transition-colors duration-1000 opacity-25"
        style={{
          background: `radial-gradient(1000px circle at 50% 15%, ${activeEpoch.accentGlow}, transparent 70%)`,
        }}
      />
      <div className="absolute inset-0 pointer-events-none bg-[radial-gradient(#ffffff08_1px,transparent_1px)] [background-size:24px_24px] opacity-40" />

      {/* Top Header & Navigation */}
      <header className="h-14 border-b border-white/10 bg-neutral-950/80 backdrop-blur-md px-6 flex items-center justify-between z-10 shrink-0">
        <div className="flex items-center gap-4">
          <button
            onClick={() => onNavigate('queue')}
            className="flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs text-neutral-400 hover:text-white hover:bg-white/5 border border-white/10 transition-colors cursor-pointer"
            title="Вернуться в очередь заявок"
          >
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M9 3L4 7L9 11" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span>В очередь</span>
          </button>

          <div className="h-4 w-px bg-white/10" />

          <div className="flex items-center gap-2.5">
            <div className="w-2 h-2 rounded-full animate-pulse" style={{ backgroundColor: activeEpoch.accentColor }} />
            <h1 className="text-sm font-semibold tracking-wide text-white uppercase">
              Хронология Инженерии IntraLink
            </h1>
            <span className="text-[11px] font-mono text-neutral-500 border border-white/10 px-2 py-0.5 rounded">
              v1.0.0 → v3.3.0
            </span>
          </div>
        </div>

        {/* Center: Mode Switcher */}
        <div className="flex items-center bg-neutral-900/90 border border-white/10 rounded-lg p-0.5">
          <button
            onClick={() => setViewMode('deck')}
            className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
              viewMode === 'deck' ? 'bg-white/10 text-white shadow-sm' : 'text-neutral-400 hover:text-neutral-200'
            }`}
          >
            <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
              <rect x="2" y="2.5" width="10" height="9" rx="1.5" />
              <path d="M5 2.5V11.5" />
            </svg>
            <span>Слайды</span>
            <kbd className="hidden sm:inline-block text-[10px] font-mono text-neutral-500 ml-1">T</kbd>
          </button>

          <button
            onClick={() => setViewMode('feed')}
            className={`flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-medium transition-colors cursor-pointer ${
              viewMode === 'feed' ? 'bg-white/10 text-white shadow-sm' : 'text-neutral-400 hover:text-neutral-200'
            }`}
          >
            <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
              <path d="M3 3h8M3 7h8M3 11h8" strokeLinecap="round" />
            </svg>
            <span>Лента релизов</span>
            <span className="text-[10px] font-mono text-neutral-500 ml-1">45+</span>
          </button>
        </div>

        {/* Right: Autoplay Controls & Short Info */}
        <div className="flex items-center gap-3">
          {viewMode === 'deck' && (
            <button
              onClick={() => setIsPlaying(p => !p)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium border transition-colors cursor-pointer ${
                isPlaying 
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' 
                  : 'border-white/10 bg-white/5 text-neutral-300 hover:text-white hover:bg-white/10'
              }`}
              title="Автоплей слайдов (Пробел)"
            >
              {isPlaying ? (
                <>
                  <svg width="12" height="12" viewBox="0 0 14 14" fill="currentColor">
                    <rect x="3" y="2" width="3" height="10" rx="0.5" />
                    <rect x="8" y="2" width="3" height="10" rx="0.5" />
                  </svg>
                  <span>Пауза</span>
                </>
              ) : (
                <>
                  <svg width="12" height="12" viewBox="0 0 14 14" fill="currentColor">
                    <polygon points="4,2 12,7 4,12" />
                  </svg>
                  <span>Автоплей</span>
                </>
              )}
              <kbd className="text-[10px] font-mono text-neutral-500 border border-white/10 px-1 rounded">Space</kbd>
            </button>
          )}

          <div className="hidden lg:flex items-center gap-1.5 text-xs text-neutral-400 font-mono">
            <span className="text-white font-semibold">265</span> задач
            <span className="text-neutral-600">•</span>
            <span className="text-white font-semibold">111K</span> строк
          </div>
        </div>
      </header>

      {/* Hero Stats Banner */}
      <div className="border-b border-white/5 bg-neutral-950/40 px-6 py-2.5 shrink-0 z-10">
        <div className="max-w-7xl mx-auto flex items-center justify-between gap-4 overflow-x-auto text-xs font-mono scrollbar-none">
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-neutral-500">Код:</span>
            <span className="text-white font-semibold">{totalStats.lines} строк</span>
          </div>
          <div className="h-3 w-px bg-white/10 shrink-0" />
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-neutral-500">Задачи:</span>
            <span className="text-white font-semibold">{totalStats.commits} коммитов</span>
          </div>
          <div className="h-3 w-px bg-white/10 shrink-0" />
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-neutral-500">Модули:</span>
            <span className="text-white font-semibold">{totalStats.modules} файлов</span>
          </div>
          <div className="h-3 w-px bg-white/10 shrink-0" />
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-neutral-500">Релизы:</span>
            <span className="text-white font-semibold">{totalStats.releases} выпусков</span>
          </div>
          <div className="h-3 w-px bg-white/10 shrink-0" />
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-neutral-500">Точность AI:</span>
            <span className="text-emerald-400 font-semibold">{totalStats.accuracy}</span>
          </div>
          <div className="h-3 w-px bg-white/10 shrink-0" />
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-neutral-500">Рутина:</span>
            <span className="text-sky-400 font-semibold">{totalStats.routineReduction}</span>
          </div>
          <div className="h-3 w-px bg-white/10 shrink-0" />
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-neutral-500">Реакция:</span>
            <span className="text-amber-400 font-semibold">{totalStats.latency}</span>
          </div>
          <div className="h-3 w-px bg-white/10 shrink-0" />
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-neutral-500">Исполнение:</span>
            <span className="text-rose-400 font-semibold">{totalStats.zeroClick}</span>
          </div>
        </div>
      </div>

      {/* Main Content Area */}
      <main className="flex-1 overflow-y-auto overflow-x-hidden min-h-0 relative z-10 p-6">
        <div className="max-w-7xl mx-auto h-full flex flex-col">
          {viewMode === 'deck' ? (
            /* ─────────────────────────────────────────────────────────────
               DECK MODE (Presentation Slides)
               ───────────────────────────────────────────────────────────── */
            <div className="flex-1 flex flex-col justify-between gap-6 min-h-0">
              {/* Epoch Header Card */}
              <div className="bg-neutral-900/60 border border-white/10 rounded-2xl p-6 backdrop-blur-md relative overflow-hidden shadow-2xl">
                {/* Progress line for autoplay */}
                {isPlaying && (
                  <div 
                    className="absolute top-0 left-0 h-1 bg-gradient-to-r from-transparent to-current transition-all duration-100 ease-linear"
                    style={{ width: `${progress}%`, color: activeEpoch.accentColor }}
                  />
                )}

                <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-3">
                      <span 
                        className="px-2.5 py-0.5 rounded-full text-xs font-mono font-bold tracking-wider uppercase border"
                        style={{ 
                          color: activeEpoch.accentColor, 
                          borderColor: `${activeEpoch.accentColor}40`,
                          backgroundColor: `${activeEpoch.accentColor}15`
                        }}
                      >
                        Эпоха {activeEpoch.roman}
                      </span>
                      <span className="text-xs font-mono text-neutral-400">
                        {activeEpoch.period}
                      </span>
                    </div>

                    <h2 className="text-2xl md:text-3xl font-bold tracking-tight text-white">
                      {activeEpoch.title}
                    </h2>
                    <p className="text-sm text-neutral-400 max-w-3xl">
                      {activeEpoch.subtitle}
                    </p>
                  </div>

                  {/* Epoch Stats Pills */}
                  <div className="flex items-center gap-3 shrink-0 flex-wrap">
                    <div className="bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-right">
                      <div className="text-xs text-neutral-500 font-mono">Объем коммитов</div>
                      <div className="text-base font-bold font-mono text-white">{activeEpoch.stats.commits} коммитов</div>
                    </div>
                    <div className="bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-right">
                      <div className="text-xs text-neutral-500 font-mono">Строк кода</div>
                      <div className="text-base font-bold font-mono text-white">{activeEpoch.stats.lines}</div>
                    </div>
                    <div className="bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-right">
                      <div className="text-xs text-neutral-500 font-mono">Главный результат</div>
                      <div className="text-base font-bold font-mono text-emerald-400">{activeEpoch.stats.keyMetric}</div>
                    </div>
                  </div>
                </div>

                <div className="mt-4 pt-4 border-t border-white/5 text-xs text-neutral-400 flex items-center justify-between">
                  <span>{activeEpoch.summary}</span>
                  <span className="font-mono text-neutral-500 shrink-0 ml-4">
                    Вех в эпохе: {activeEpoch.milestones.length}
                  </span>
                </div>
              </div>

              {/* Milestones Grid in Current Epoch */}
              <div className="flex-1 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 auto-rows-fr min-h-0 overflow-y-auto pr-1">
                {activeEpoch.milestones.map((m) => (
                  <div
                    key={m.id}
                    onClick={() => setActiveMilestone(m)}
                    className="group relative bg-neutral-900/40 hover:bg-neutral-900/80 border border-white/10 hover:border-white/20 rounded-xl p-4 transition-all duration-200 cursor-pointer flex flex-col justify-between hover:shadow-xl hover:-translate-y-0.5"
                  >
                    <div className="space-y-2.5">
                      <div className="flex items-center justify-between gap-2">
                        <span 
                          className="text-xs font-mono font-bold px-2 py-0.5 rounded border"
                          style={{ 
                            color: activeEpoch.accentColor, 
                            borderColor: `${activeEpoch.accentColor}30`,
                            backgroundColor: `${activeEpoch.accentColor}10`
                          }}
                        >
                          {m.version}
                        </span>
                        <span className="text-[11px] font-mono text-neutral-500">
                          {m.date}
                        </span>
                      </div>

                      <h3 className="text-sm font-semibold text-white group-hover:text-neutral-100 line-clamp-2 leading-snug">
                        {m.title}
                      </h3>

                      <p className="text-xs text-neutral-400 line-clamp-3 leading-relaxed">
                        {m.solution}
                      </p>
                    </div>

                    <div className="mt-3 pt-3 border-t border-white/5 flex items-center justify-between text-[11px]">
                      <span className="text-neutral-500 font-mono truncate max-w-[140px]">
                        {m.categoryLabel}
                      </span>
                      <div className="flex items-center gap-1 text-sky-400 opacity-0 group-hover:opacity-100 transition-opacity font-mono">
                        <span>Детали</span>
                        <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                          <path d="M5 3l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {/* Deck Navigation Controls */}
              <div className="flex items-center justify-between pt-2 shrink-0">
                <button
                  onClick={() => setActiveEpochIndex(idx => Math.max(idx - 1, 0))}
                  disabled={activeEpochIndex === 0}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-medium border border-white/10 bg-neutral-900/60 hover:bg-neutral-800 disabled:opacity-30 disabled:pointer-events-none transition-colors cursor-pointer"
                >
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M9 3L4 7L9 11" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <span>Предыдущая эпоха</span>
                  <kbd className="text-[10px] font-mono text-neutral-500 ml-1">←</kbd>
                </button>

                {/* Step Indicators */}
                <div className="flex items-center gap-2">
                  {EPOCHS.map((ep, index) => (
                    <button
                      key={ep.id}
                      onClick={() => setActiveEpochIndex(index)}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono transition-all cursor-pointer ${
                        index === activeEpochIndex
                          ? 'bg-white/15 text-white font-bold border border-white/20 shadow-sm'
                          : 'text-neutral-500 hover:text-neutral-300 hover:bg-white/5 border border-transparent'
                      }`}
                    >
                      <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: ep.accentColor }} />
                      <span>{ep.roman}</span>
                    </button>
                  ))}
                </div>

                <button
                  onClick={() => setActiveEpochIndex(idx => Math.min(idx + 1, EPOCHS.length - 1))}
                  disabled={activeEpochIndex === EPOCHS.length - 1}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-medium border border-white/10 bg-neutral-900/60 hover:bg-neutral-800 disabled:opacity-30 disabled:pointer-events-none transition-colors cursor-pointer"
                >
                  <span>Следующая эпоха</span>
                  <kbd className="text-[10px] font-mono text-neutral-500 mr-1">→</kbd>
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M5 3L10 7L5 11" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
              </div>
            </div>
          ) : (
            /* ─────────────────────────────────────────────────────────────
               FEED MODE (Continuous Timeline View)
               ───────────────────────────────────────────────────────────── */
            <div className="space-y-6 pb-20">
              {/* Filter and Search Bar */}
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-neutral-900/60 border border-white/10 rounded-xl p-4 backdrop-blur-md sticky top-0 z-20 shadow-xl">
                {/* Categories */}
                <div className="flex items-center gap-1.5 flex-wrap">
                  {[
                    { id: 'all', label: 'Все (45+)' },
                    { id: 'core', label: 'Ядро и БД' },
                    { id: 'ai', label: 'AI и RAG' },
                    { id: 'workers', label: 'Роботы' },
                    { id: 'ui', label: 'Веб-пульт' },
                    { id: 'security', label: 'Безопасность' },
                  ].map(cat => (
                    <button
                      key={cat.id}
                      onClick={() => setSelectedCategory(cat.id as CategoryType)}
                      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer ${
                        selectedCategory === cat.id
                          ? 'bg-sky-500/20 text-sky-300 border border-sky-500/40 font-semibold'
                          : 'text-neutral-400 hover:text-white hover:bg-white/5 border border-transparent'
                      }`}
                    >
                      {cat.label}
                    </button>
                  ))}
                </div>

                {/* Search */}
                <div className="relative min-w-[240px]">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={e => setSearchQuery(e.target.value)}
                    placeholder="Поиск по названию или решению..."
                    className="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white placeholder-neutral-500 focus:outline-none focus:border-sky-500/50"
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery('')}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-500 hover:text-white"
                    >
                      <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                        <path d="M3 3l8 8M11 3l-8 8" />
                      </svg>
                    </button>
                  )}
                </div>
              </div>

              {/* Vertical Feed Timeline */}
              <div className="relative pl-6 md:pl-8 space-y-6 before:absolute before:left-3 md:before:left-4 before:top-2 before:bottom-2 before:w-0.5 before:bg-gradient-to-b before:from-sky-500 before:via-purple-500 before:to-rose-500 before:opacity-40">
                {filteredMilestones.map((m) => (
                  <div
                    key={m.id}
                    onClick={() => setActiveMilestone(m)}
                    className="group relative bg-neutral-900/50 hover:bg-neutral-900/90 border border-white/10 hover:border-white/20 rounded-xl p-5 transition-all duration-200 cursor-pointer shadow-lg hover:shadow-2xl"
                  >
                    {/* Node Dot on Timeline */}
                    <div 
                      className="absolute -left-[27px] md:-left-[35px] top-6 w-3 h-3 rounded-full border-2 border-neutral-950 transition-transform group-hover:scale-125"
                      style={{ backgroundColor: m.epochAccent }}
                    />

                    <div className="flex flex-col md:flex-row md:items-start justify-between gap-3">
                      <div className="space-y-1.5 flex-1">
                        <div className="flex items-center gap-3">
                          <span 
                            className="text-xs font-mono font-bold px-2 py-0.5 rounded border"
                            style={{ 
                              color: m.epochAccent, 
                              borderColor: `${m.epochAccent}40`,
                              backgroundColor: `${m.epochAccent}10`
                            }}
                          >
                            {m.version}
                          </span>
                          <span className="text-xs font-mono text-neutral-500">{m.date}</span>
                          <span className="text-xs font-mono text-neutral-500 border border-white/10 px-2 py-0.5 rounded">
                            {m.categoryLabel}
                          </span>
                          <span className="text-[11px] font-mono text-neutral-500">
                            Эпоха {m.epochRoman}
                          </span>
                        </div>

                        <h3 className="text-base font-semibold text-white group-hover:text-sky-300 transition-colors">
                          {m.title}
                        </h3>

                        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3 text-xs">
                          <div className="bg-black/30 border border-white/5 rounded-lg p-3">
                            <span className="text-neutral-500 font-mono block mb-1">Проблема («Что было до этого»):</span>
                            <p className="text-neutral-300 leading-relaxed">{m.problem}</p>
                          </div>
                          <div className="bg-black/30 border border-white/5 rounded-lg p-3">
                            <span className="text-emerald-400/80 font-mono block mb-1">Инженерное решение:</span>
                            <p className="text-neutral-200 leading-relaxed">{m.solution}</p>
                          </div>
                        </div>
                      </div>

                      <div className="flex flex-col items-end justify-between gap-2 shrink-0 md:min-w-[160px] pt-1">
                        <span className="text-xs font-mono text-emerald-400 font-semibold bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 rounded text-right">
                          {m.impact}
                        </span>
                        <div className="flex items-center gap-1.5 text-xs text-neutral-400 font-mono group-hover:text-white transition-colors">
                          <span>Открыть схему</span>
                          <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                            <path d="M5 3l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </main>

      {/* Interactive Scrubber Bar (Bottom) */}
      <footer className="h-16 border-t border-white/10 bg-neutral-950/90 backdrop-blur-md px-6 flex items-center justify-between gap-6 z-10 shrink-0">
        <div className="text-xs font-mono text-neutral-500 shrink-0 hidden md:block">
          Эволюционный трекер IntraLink
        </div>

        {/* 5 Epochs Progress Line */}
        <div className="flex-1 max-w-4xl mx-auto flex items-center justify-between gap-3 relative">
          <div className="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-0.5 bg-white/10" />

          {EPOCHS.map((ep, idx) => {
            const isSelected = idx === activeEpochIndex;
            return (
              <button
                key={ep.id}
                onClick={() => {
                  setActiveEpochIndex(idx);
                  if (viewMode === 'feed') setViewMode('deck');
                }}
                className={`group relative z-10 flex flex-col items-center gap-1 transition-all cursor-pointer`}
              >
                <div 
                  className={`w-4 h-4 rounded-full border-2 transition-all flex items-center justify-center ${
                    isSelected 
                      ? 'scale-125 border-white bg-neutral-950 shadow-lg' 
                      : 'border-neutral-700 bg-neutral-900 group-hover:border-neutral-400'
                  }`}
                  style={{ borderColor: isSelected ? ep.accentColor : undefined }}
                >
                  <div 
                    className="w-1.5 h-1.5 rounded-full" 
                    style={{ backgroundColor: ep.accentColor }} 
                  />
                </div>

                <div className="flex flex-col items-center">
                  <span className={`text-[11px] font-mono font-bold transition-colors ${
                    isSelected ? 'text-white' : 'text-neutral-500 group-hover:text-neutral-300'
                  }`}>
                    {ep.roman}
                  </span>
                  <span className="text-[10px] text-neutral-500 hidden sm:block truncate max-w-[120px]">
                    {ep.shortPeriod}
                  </span>
                </div>
              </button>
            );
          })}
        </div>

        <div className="text-xs font-mono text-neutral-500 shrink-0">
          <kbd className="border border-white/10 px-1.5 py-0.5 rounded text-[10px]">1..5</kbd> эпохи
        </div>
      </footer>

      {/* ─────────────────────────────────────────────────────────────
          MILESTONE DRAWER / INSPECTOR MODAL
          ───────────────────────────────────────────────────────────── */}
      {activeMilestone && (
        <div className="fixed inset-0 z-50 flex items-center justify-end bg-black/70 backdrop-blur-sm animate-fade-in">
          {/* Backdrop Click to close */}
          <div className="absolute inset-0" onClick={() => setActiveMilestone(null)} />

          {/* Drawer Body */}
          <div className="relative w-full max-w-2xl h-full bg-[#0d1117] border-l border-white/10 p-8 overflow-y-auto flex flex-col justify-between shadow-2xl z-10 animate-slide-left">
            <div className="space-y-6">
              {/* Drawer Header */}
              <div className="flex items-start justify-between gap-4 border-b border-white/10 pb-5">
                <div className="space-y-2">
                  <div className="flex items-center gap-3">
                    <span className="px-2.5 py-0.5 rounded text-xs font-mono font-bold bg-sky-500/10 text-sky-400 border border-sky-500/30">
                      {activeMilestone.version}
                    </span>
                    <span className="text-xs font-mono text-neutral-400">
                      {activeMilestone.date}
                    </span>
                    <span className="text-xs font-mono text-neutral-500 border border-white/10 px-2 py-0.5 rounded">
                      {activeMilestone.categoryLabel}
                    </span>
                  </div>

                  <h2 className="text-xl font-bold text-white leading-tight">
                    {activeMilestone.title}
                  </h2>
                </div>

                <button
                  onClick={() => setActiveMilestone(null)}
                  className="w-8 h-8 rounded-lg border border-white/10 flex items-center justify-center text-neutral-400 hover:text-white hover:bg-white/10 transition-colors cursor-pointer"
                  title="Закрыть (Esc)"
                >
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M3 3l8 8M11 3l-8 8" />
                  </svg>
                </button>
              </div>

              {/* Three Strategic Blocks */}
              <div className="space-y-4">
                {/* 1. Problem */}
                <div className="bg-neutral-900/60 border border-rose-500/20 rounded-xl p-4 space-y-1.5">
                  <div className="flex items-center gap-2 text-xs font-mono text-rose-400 uppercase font-semibold">
                    <span className="w-1.5 h-1.5 rounded-full bg-rose-400" />
                    <span>Что было до этого (Проблема)</span>
                  </div>
                  <p className="text-xs text-neutral-300 leading-relaxed">
                    {activeMilestone.problem}
                  </p>
                </div>

                {/* 2. Solution */}
                <div className="bg-neutral-900/60 border border-sky-500/20 rounded-xl p-4 space-y-1.5">
                  <div className="flex items-center gap-2 text-xs font-mono text-sky-400 uppercase font-semibold">
                    <span className="w-1.5 h-1.5 rounded-full bg-sky-400" />
                    <span>Инженерное решение (Что сделано)</span>
                  </div>
                  <p className="text-xs text-neutral-200 leading-relaxed">
                    {activeMilestone.solution}
                  </p>
                </div>

                {/* 3. Impact */}
                <div className="bg-neutral-900/60 border border-emerald-500/20 rounded-xl p-4 space-y-1.5">
                  <div className="flex items-center gap-2 text-xs font-mono text-emerald-400 uppercase font-semibold">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                    <span>Результат и польза (В цифрах)</span>
                  </div>
                  <p className="text-xs text-emerald-300/90 font-mono leading-relaxed">
                    {activeMilestone.impact}
                  </p>
                </div>
              </div>

              {/* Architectural Vector Flowchart */}
              <div className="bg-black/50 border border-white/10 rounded-xl p-4 space-y-3">
                <div className="flex items-center justify-between text-xs font-mono text-neutral-400">
                  <span>Архитектурная схема взаимодействия:</span>
                  <span className="text-[10px] text-neutral-600">Векторный поток данных</span>
                </div>

                <div className="p-4 bg-[#080b10] border border-white/5 rounded-lg flex items-center justify-center">
                  {/* SVG Architecture Diagram */}
                  <svg width="100%" height="80" viewBox="0 0 460 80" fill="none" className="max-w-md">
                    {/* Step 1 */}
                    <rect x="10" y="20" width="100" height="40" rx="6" fill="#141922" stroke="#38bdf8" strokeWidth="1.2" />
                    <text x="60" y="44" fill="#f8fafc" fontSize="11" fontFamily="Inter" textAnchor="middle">Входящий тикет</text>

                    {/* Arrow 1 */}
                    <line x1="110" y1="40" x2="160" y2="40" stroke="#64748b" strokeWidth="1.2" strokeDasharray="3 3" />
                    <polygon points="160,40 154,37 154,43" fill="#64748b" />

                    {/* Step 2 */}
                    <rect x="165" y="15" width="130" height="50" rx="6" fill="#181426" stroke="#a855f7" strokeWidth="1.2" />
                    <text x="230" y="38" fill="#e9d5ff" fontSize="11" fontFamily="Inter" fontWeight="bold" textAnchor="middle">{activeMilestone.categoryLabel}</text>
                    <text x="230" y="53" fill="#a855f7" fontSize="9" fontFamily="JetBrains Mono" textAnchor="middle">{activeMilestone.version}</text>

                    {/* Arrow 2 */}
                    <line x1="295" y1="40" x2="345" y2="40" stroke="#64748b" strokeWidth="1.2" strokeDasharray="3 3" />
                    <polygon points="345,40 339,37 339,43" fill="#64748b" />

                    {/* Step 3 */}
                    <rect x="350" y="20" width="100" height="40" rx="6" fill="#0f231d" stroke="#34d399" strokeWidth="1.2" />
                    <text x="400" y="44" fill="#6ee7b7" fontSize="11" fontFamily="Inter" textAnchor="middle">Готовое решение</text>
                  </svg>
                </div>
              </div>

              {/* Modified Modules */}
              <div className="space-y-2">
                <span className="text-xs font-mono text-neutral-400 block">
                  Затронутые модули монорепозитория:
                </span>
                <div className="space-y-1 font-mono text-xs">
                  {activeMilestone.modules.map(mod => (
                    <div key={mod} className="bg-black/40 border border-white/5 px-3 py-1.5 rounded text-neutral-300 flex items-center justify-between">
                      <span className="truncate">{mod}</span>
                      <span className="text-[10px] text-neutral-500">production</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* Drawer Footer Actions */}
            <div className="pt-6 mt-6 border-t border-white/10 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-neutral-500">Коммит:</span>
                <button
                  onClick={() => handleCopyRef(activeMilestone.commitRef)}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-white/5 hover:bg-white/10 border border-white/10 text-xs font-mono text-neutral-300 hover:text-white transition-colors cursor-pointer"
                  title="Скопировать ref коммита"
                >
                  <span>ref: {activeMilestone.commitRef}</span>
                  {copiedRef === activeMilestone.commitRef ? (
                    <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="#34d399" strokeWidth="2">
                      <path d="M2 7l3.5 3.5L12 3" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  ) : (
                    <svg width="12" height="12" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.4">
                      <rect x="4" y="4" width="8" height="8" rx="1.5" />
                      <path d="M10 4V2.5A1.5 1.5 0 008.5 1h-6A1.5 1.5 0 001 2.5v6A1.5 1.5 0 002.5 10H4" />
                    </svg>
                  )}
                </button>
                {copiedRef === activeMilestone.commitRef && (
                  <span className="text-xs font-mono text-emerald-400 animate-fade-in">
                    Скопировано!
                  </span>
                )}
              </div>

              <button
                onClick={() => setActiveMilestone(null)}
                className="px-4 py-1.5 rounded-lg text-xs font-medium bg-white/10 hover:bg-white/20 text-white transition-colors cursor-pointer"
              >
                Закрыть карточку
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
