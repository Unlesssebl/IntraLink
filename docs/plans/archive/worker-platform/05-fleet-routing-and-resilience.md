# Инкремент 5: Worker Fleet, Routing Quarantine и Отказоустойчивость

Статус: ✅ Завершен  
Дата завершения: 2026-09-07  
Зависимости: [Инкремент 1](01-contract-and-core-safety.md), [Инкремент 2](02-worker-sdk-and-runner.md), [Инкремент 4](04-printer-pilot-and-staging.md)  
Целевые компоненты: `execution-worker/worker.py`, `core-api/app/routers/commands_v2.py`, `core-api/app/services/command_outbox.py`, `core-api/tests/`

---

## 1. Цель инкремента

Завершить формирование распределенной платформы исполнения:
- Внедрить модель регистрации узлов Worker Fleet в Redis.
- Реализовать маршрутизацию по пулам возможностей (capability-based routing) на уровне Core API Outbox Relay.
- Внедрить механизм Routing Quarantine в Core API для исключения зависаний несовместимых задач в PEL.
- Провести комплексные стресс-тесты и отказные сценарии (Gate A, B, C, D).

---

## 2. Задачи к реализации

### 2.1. Регистрация узлов флота воркеров
- Воркер при старте и каждые 10 секунд обновляет карточку узла в Redis:
  - Ключ: `worker:node:<node_id>` (TTL = 30 сек).
  - Набор: `worker:nodes` (SADD node_id).
  - Поля карточки:
    ```json
    {
      "node_id": "win-node-spb-01",
      "hostname": "SRV-WORKER-01",
      "version": "0.2.0",
      "commit_sha": "de940fd",
      "domain": "CORP.LOCAL",
      "network_zones": ["192.168.0.0/16"],
      "capabilities": ["winrm", "powershell", "printers", "ad"],
      "supported_actions": {
        "diagnose_host": "1.0",
        "install_printer": "2.0",
        "grant_wlan": "1.0"
      },
      "max_concurrency": 8,
      "active_slots": 2,
      "last_heartbeat_at": 1757239000.0
    }
    ```

### 2.2. Серверная маршрутизация по пулам воркеров
- В Core API Outbox Relay перед публикацией команды вычисляется целевой stream pool:
  - Для универсальных Windows-команд: `stream:execution_commands:v2:windows`.
  - Для специализированных пулов (AD, сетевые зоны): соответствующий подстрим.
- Выбранный `routing_key` сохраняется в записи Outbox и событиях команды в PostgreSQL.

### 2.3. Идемпотентный Routing Quarantine (Защита от PEL-голодания)
- Если воркер случайно вычитал команду, требующую capability, которой у него нет:
  1. Воркер **не удерживает** сообщение в своем PEL.
  2. Воркер вызывает серверный эндпоинт:
     ```http
     POST /api/v2/commands/{id}/quarantine
     {
       "worker_id": "win-node-01",
       "missing_capability": "ad_tools",
       "message_id": "1757239000-0"
     }
     ```
  3. Core API атомарно в PostgreSQL перенаправляет команду в подходящий стрим либо помечает ошибку маршрутизации.
  4. Только после получения успешного ответа 200 OK воркер выполняет `XACK`.
  5. Самостоятельные клиентские циклы `XADD + XACK` на стороне воркера запрещены.

### 2.4. Наблюдаемость флота (Observability)
- В Core API эндпоинты:
  - `GET /api/v2/workers/fleet` — список активных узлов, их загрузка, возможности и время последнего heartbeat.
  - `GET /api/v2/workers/readiness` — разделение readiness сервиса: Core API готов, но статус готовности Windows Execution зависит от наличия живых узлов с нужными capabilities.

### 2.5. Комплексный набор стресс-тестов отказоустойчивости
- Сценарий 1: падение воркера во время фазы `execute` -> lease истекает -> команда переходит в `needs_review` без автоповтора.
- Сценарий 2: потеря Redis во время выполнения -> mutating-команда переходит в fail-closed до наступления side effect.
- Сценарий 3: два воркера на один ПК -> второй воркер отклоняется на этапе захвата `Host Lock`.
- Сценарий 4: рассинхронизация часов -> монотонный таймер и относительный TTL предотвращают преждевременную экспирацию.
- Сценарий 5: дрейф конфигурации WinRM -> cleanup прерывается, формируется operational alert.

---

## 3. Критерии приемки (Definition of Done)

1. Все контрольные точки Gate A, Gate B, Gate C, Gate D успешно пройдены.
2. Несовместимые задачи гарантированно не зависают в PEL и безопасно изолируются через карантин.
3. Административный UI или CLI отображает список всех доступных узлов флота и их текущие возможности.
4. Отсутствие регрессий в существующих модулях `core-api`, `shared` и `telegram-bot`.
