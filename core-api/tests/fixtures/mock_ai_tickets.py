"""
Синтетический набор мок-заявок IntraService для всестороннего тестирования AI-контура:
- Zero Trust DLP Sanitizer (RED, YELLOW, GREEN)
- PII Masking и Vault Rehydration
- Hybrid RAG (Dense + Sparse BM25 + Cross-Encoder Reranker)
- Query Distillation (отсечение эмоционального шума)
- Auto-KB Canonization и Response Synthesis
- Детекция массовых инцидентов (Outage Clustering)
"""

from typing import Any, Dict, List, Optional
from app.services.ai.schemas import DataCircuit

MOCK_AI_TICKETS: List[Dict[str, Any]] = [
    # -----------------------------------------------------------------------
    # 1. RED CIRCUIT: Заявки с паролями, секретами и токенами (Строго локально)
    # -----------------------------------------------------------------------
    {
        "Id": 140001,
        "Name": "Сброс пароля доменной учетной записи",
        "Description": (
            "Добрый день. Пользователь забыл пароль от Windows. "
            "Установите временный пароль: TempPass2026! для входа."
        ),
        "Created": "2026-09-02T09:15:00",
        "StatusId": 26,
        "StatusName": "Новая",
        "ServiceId": 42,
        "ServiceName": "01. Учетные записи",
        "Creator": "Кузнецов Дмитрий Сергеевич",
        "CreatorPhone": "11-22",
        "CustomFields": [
            {"Name": "Кабинет", "Value": "301"},
            {"Name": "Имя ПК", "Value": "NTEMW0110"},
        ],
        "Lifetime": [
            {"UserName": "Кузнецов Д.С.", "Created": "2026-09-02T09:15:00", "Text": "Создана заявка"}
        ],
        "expected_circuit": DataCircuit.RED,
        "expected_category": "credentials",
        "test_notes": "Содержит ключевое слово 'пароль: TempPass2026!' -> должен роутиться строго в RED/Ollama",
    },
    {
        "Id": 140002,
        "Name": "Интеграция со шлюзом: передача API токена",
        "Description": (
            "Коллеги, для настройки интеграции используйте токен авторизации "
            "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-zR7 "
            "на сервере srv-api.corp.local."
        ),
        "Created": "2026-09-02T09:30:00",
        "StatusId": 26,
        "StatusName": "Новая",
        "ServiceId": 42,
        "ServiceName": "01. Учетные записи",
        "Creator": "Смирнов Алексей",
        "CreatorPhone": "44-55",
        "CustomFields": [],
        "Lifetime": [],
        "expected_circuit": DataCircuit.RED,
        "expected_category": "credentials",
        "test_notes": "Содержит Bearer JWT токен -> RED контур",
    },
    {
        "Id": 140003,
        "Name": "Учетная запись 1С:Бухгалтерия",
        "Description": (
            "Создать пользователя для нового бухгалтера. "
            "Логин: buh_novikova, password = SecretKey_777, база 'Бухгалтерия КОРП'."
        ),
        "Created": "2026-09-02T09:45:00",
        "StatusId": 26,
        "StatusName": "Новая",
        "ServiceId": 42,
        "ServiceName": "01. Учетные записи",
        "Creator": "Новикова Анна",
        "CreatorPhone": "33-44",
        "CustomFields": [],
        "Lifetime": [],
        "expected_circuit": DataCircuit.RED,
        "expected_category": "credentials",
        "test_notes": "Содержит 'password = ...' -> RED контур",
    },

    # -----------------------------------------------------------------------
    # 2. YELLOW CIRCUIT: Персональные данные, хосты, IP (Маскирование -> Cloud)
    # -----------------------------------------------------------------------
    {
        "Id": 140004,
        "Name": "Сетевой диск не подключается",
        "Description": (
            "Здравствуйте! Сотрудник Ковалев Артем Игоревич не может получить доступ к общей папке "
            "\\\\srv-files.corp.local\\finances на рабочей станции NTEMW0144 с IP-адресом 10.244.15.82. "
            "Телефон для связи: +7 (999) 123-45-67, почта kovalev@company.ru."
        ),
        "Created": "2026-09-02T10:00:00",
        "StatusId": 26,
        "StatusName": "Новая",
        "ServiceId": 45,
        "ServiceName": "03. Сетевые ресурсы",
        "Creator": "Ковалев А.И.",
        "CreatorPhone": "+7 (999) 123-45-67",
        "CustomFields": [
            {"Name": "Имя ПК", "Value": "NTEMW0144"},
            {"Name": "IP адрес", "Value": "10.244.15.82"},
            {"Name": "Кабинет", "Value": "402"},
        ],
        "Lifetime": [],
        "expected_circuit": DataCircuit.YELLOW,
        "expected_category": "network",
        "test_notes": "Содержит ФИО, IP 10.244.15.82, хост NTEMW0144, email и телефон -> проверка маскирования и деанонимизации",
    },
    {
        "Id": 140005,
        "Name": "Настройка сетевого МФУ для отдела продаж",
        "Description": (
            "Прошу настроить печать на сетевой принтер HP LaserJet Pro M404dn (IP 10.244.20.15) "
            "для сотрудника Сидорова Елена Михайловна на ноутбуке LAPTOP-B891CC в кабинете 215. "
            "Контактный телефон: 89031112233."
        ),
        "Created": "2026-09-02T10:15:00",
        "StatusId": 26,
        "StatusName": "Новая",
        "ServiceId": 43,
        "ServiceName": "02. Оргтехника и печать",
        "Creator": "Сидорова Е.М.",
        "CreatorPhone": "89031112233",
        "CustomFields": [
            {"Name": "Имя ПК", "Value": "LAPTOP-B891CC"},
            {"Name": "IP принтера", "Value": "10.244.20.15"},
        ],
        "Lifetime": [],
        "expected_circuit": DataCircuit.YELLOW,
        "expected_category": "printer",
        "test_notes": "Содержит ФИО, внутренний IP и хост LAPTOP -> маскирование в YELLOW",
    },

    # -----------------------------------------------------------------------
    # 3. GREEN CIRCUIT: Общие технические вопросы без конфиденциальных данных
    # -----------------------------------------------------------------------
    {
        "Id": 140006,
        "Name": "Как включить темную тему в Windows 11",
        "Description": (
            "Подскажите, пожалуйста, как в стандартной операционной системе Windows 11 "
            "переключить оформление интерфейса со светлой темы на темную?"
        ),
        "Created": "2026-09-02T10:30:00",
        "StatusId": 26,
        "StatusName": "Новая",
        "ServiceId": 50,
        "ServiceName": "Консультация",
        "Creator": "Аноним",
        "CreatorPhone": "",
        "CustomFields": [],
        "Lifetime": [],
        "expected_circuit": DataCircuit.GREEN,
        "expected_category": "generic",
        "test_notes": "Не содержит PII, паролей или внутренних хостов -> прямой Cloud GREEN инференс",
    },

    # -----------------------------------------------------------------------
    # 4. CANONICAL RAG EXAMPLES: Исторические закрытые заявки с решениями
    # -----------------------------------------------------------------------
    {
        "Id": 139501,
        "Name": "Ошибка 0x0000011b при печати на сетевой принтер Kyocera",
        "Description": "После вчерашнего обновления Windows перестали печатать документы на принтер Kyocera ECOSYS M2040dn.",
        "Created": "2026-08-20T11:00:00",
        "StatusId": 29,
        "StatusName": "Выполнена",
        "ServiceId": 43,
        "ServiceName": "02. Оргтехника и печать",
        "Creator": "Мельников В.В.",
        "CreatorPhone": "12-34",
        "CustomFields": [{"Name": "Имя ПК", "Value": "NTEMW0055"}],
        "Lifetime": [
            {
                "UserName": "Беликов Ален",
                "Created": "2026-08-20T11:30:00",
                "Comment": (
                    "Проблема вызвана обновлением безопасности RPC. "
                    "Удалено обновление KB5005565, прописан ключ реестра HKLM\\SYSTEM\\CurrentControlSet\\Control\\Print: "
                    "RpcAuthnLevelExemption=0 (DWORD). Служба Spooler перезапущена, печать восстановлена."
                ),
                "Text": (
                    "Проблема вызвана обновлением безопасности RPC. "
                    "Удалено обновление KB5005565, прописан ключ реестра HKLM\\SYSTEM\\CurrentControlSet\\Control\\Print: "
                    "RpcAuthnLevelExemption=0 (DWORD). Служба Spooler перезапущена, печать восстановлена."
                ),
            }
        ],
        "expected_circuit": DataCircuit.GREEN,
        "expected_category": "printer",
        "test_notes": "Канонический прецедент RAG для сбоев сетевой печати RPC 0x0000011b",
    },
    {
        "Id": 139502,
        "Name": "Ошибка формата потока при запуске 1С:Предприятие",
        "Description": "При открытии базы 1С Бухгалтерия на ПК NTEMW0089 появляется окно 'Ошибка формата потока'.",
        "Created": "2026-08-21T08:30:00",
        "StatusId": 29,
        "StatusName": "Выполнена",
        "ServiceId": 44,
        "ServiceName": "04. 1С:Предприятие",
        "Creator": "Григорьева О.П.",
        "CreatorPhone": "55-66",
        "CustomFields": [{"Name": "Имя ПК", "Value": "NTEMW0089"}],
        "Lifetime": [
            {
                "UserName": "Беликов Ален",
                "Created": "2026-08-21T08:50:00",
                "Comment": (
                    "Поврежден локальный кэш конфигурации 1С. "
                    "Очищены каталоги %LOCALAPPDATA%\\1C\\1cv8 и %APPDATA%\\1C\\1cv8, удалены временные файлы базы. "
                    "Вход в 1С успешно восстановлен."
                ),
                "Text": (
                    "Поврежден локальный кэш конфигурации 1С. "
                    "Очищены каталоги %LOCALAPPDATA%\\1C\\1cv8 и %APPDATA%\\1C\\1cv8, удалены временные файлы базы. "
                    "Вход в 1С успешно восстановлен."
                ),
            }
        ],
        "expected_circuit": DataCircuit.YELLOW,
        "expected_category": "1c",
        "test_notes": "Канонический прецедент RAG по сбою кэша 1С",
    },
    {
        "Id": 139503,
        "Name": "Подключение нового смартфона к корпоративному Wi-Fi",
        "Description": "Не подключается служебный смартфон сотрудника Тимофеев К.С. (тел. +7 999 777-88-99) к беспроводной сети WLAN-WORKNET в офисе.",
        "Created": "2026-08-22T14:00:00",
        "StatusId": 29,
        "StatusName": "Выполнена",
        "ServiceId": 45,
        "ServiceName": "03. Сетевые ресурсы",
        "Creator": "Тимофеев К.С.",
        "CreatorPhone": "77-88",
        "CustomFields": [],
        "Lifetime": [
            {
                "UserName": "Беликов Ален",
                "Created": "2026-08-22T14:15:00",
                "Comment": (
                    "Устройство добавлено в доменную группу доступа WLAN-WORKNET через контроллер домена. "
                    "Выполнен перевыпуск сертификата, авторизация по 802.1X успешна."
                ),
                "Text": (
                    "Устройство добавлено в доменную группу доступа WLAN-WORKNET через контроллер домена. "
                    "Выполнен перевыпуск сертификата, авторизация по 802.1X успешна."
                ),
            }
        ],
        "expected_circuit": DataCircuit.YELLOW,
        "expected_category": "wifi",
        "test_notes": "Канонический прецедент для Wi-Fi / LDAPS",
    },

    # -----------------------------------------------------------------------
    # 5. NOISE & EMOTIONAL: Проверка алгоритма Query Distillation
    # -----------------------------------------------------------------------
    {
        "Id": 140007,
        "Name": "СРОЧНО ПОМОГИТЕ ВСЁ ПРОПАЛО! Ошибка печати",
        "Description": (
            "Здравствуйте уважаемые специалисты техподдержки! Пожалуйста помогите умоляю! "
            "Шеф ругается, все пропало, я в панике! "
            "При попытке распечатать на принтер Kyocera M2040dn с компьютера NTEMW0199 выскакивает ошибка 0x0000011b! "
            "Сделайте что-нибудь скорей пожалуйста, заранее огромное человеческое спасибо!"
        ),
        "Created": "2026-09-02T11:00:00",
        "StatusId": 26,
        "StatusName": "Новая",
        "ServiceId": 43,
        "ServiceName": "02. Оргтехника и печать",
        "Creator": "Павлова Светлана",
        "CreatorPhone": "99-00",
        "CustomFields": [{"Name": "Имя ПК", "Value": "NTEMW0199"}],
        "Lifetime": [],
        "expected_circuit": DataCircuit.YELLOW,
        "expected_category": "noise",
        "test_notes": "Query Distillation должен удалить панику/приветствия и оставить 'Kyocera M2040dn ошибка 0x0000011b'",
    },

    # -----------------------------------------------------------------------
    # 6. MASS OUTAGE CLUSTER: Заявки для тестирования кластеризации аварий
    # -----------------------------------------------------------------------
    {
        "Id": 140010,
        "Name": "Сервер 1С не отвечает",
        "Description": "При запуске 1С Бухгалтерия ошибка: Не удалось установить соединение с сервером srv-1c.corp.local:1541",
        "Created": "2026-09-02T11:20:00",
        "StatusId": 26,
        "StatusName": "Новая",
        "ServiceId": 44,
        "ServiceName": "04. 1С:Предприятие",
        "Creator": "Бухгалтер 1",
        "CreatorPhone": "10-01",
        "CustomFields": [],
        "Lifetime": [],
        "expected_circuit": DataCircuit.YELLOW,
        "expected_category": "outage",
        "test_notes": "Кластер аварии 1С (заявка 1)",
    },
    {
        "Id": 140011,
        "Name": "У всего отдела упала 1С Бухгалтерия",
        "Description": "Вся бухгалтерия вылетела из 1С с ошибкой подключения к серверу баз данных srv-1c.corp.local.",
        "Created": "2026-09-02T11:22:00",
        "StatusId": 26,
        "StatusName": "Новая",
        "ServiceId": 44,
        "ServiceName": "04. 1С:Предприятие",
        "Creator": "Главный Бухгалтер",
        "CreatorPhone": "10-00",
        "CustomFields": [],
        "Lifetime": [],
        "expected_circuit": DataCircuit.YELLOW,
        "expected_category": "outage",
        "test_notes": "Кластер аварии 1С (заявка 2)",
    },
    {
        "Id": 140012,
        "Name": "Сбой сервера 1С 1541",
        "Description": "База данных недоступна, порт 1541 не отвечает на srv-1c.corp.local.",
        "Created": "2026-09-02T11:25:00",
        "StatusId": 26,
        "StatusName": "Новая",
        "ServiceId": 44,
        "ServiceName": "04. 1С:Предприятие",
        "Creator": "Экономист",
        "CreatorPhone": "10-02",
        "CustomFields": [],
        "Lifetime": [],
        "expected_circuit": DataCircuit.YELLOW,
        "expected_category": "outage",
        "test_notes": "Кластер аварии 1С (заявка 3)",
    },
]


def get_mock_tasks() -> List[Dict[str, Any]]:
    """Возвращает полный набор синтетических мок-заявок."""
    return [dict(t) for t in MOCK_AI_TICKETS]


def get_mock_task_by_id(task_id: int) -> Optional[Dict[str, Any]]:
    """Поиск мок-заявки по Id."""
    for t in MOCK_AI_TICKETS:
        if t["Id"] == task_id:
            return dict(t)
    return None


def get_mock_tasks_by_circuit(circuit: DataCircuit) -> List[Dict[str, Any]]:
    """Фильтрация мок-заявок по ожидаемому контуру безопасности (RED, YELLOW, GREEN)."""
    return [dict(t) for t in MOCK_AI_TICKETS if t.get("expected_circuit") == circuit]


def get_mock_tasks_by_category(category: str) -> List[Dict[str, Any]]:
    """Фильтрация мок-заявок по категории (credentials, printer, 1c, wifi, noise, outage)."""
    return [dict(t) for t in MOCK_AI_TICKETS if t.get("expected_category") == category]
