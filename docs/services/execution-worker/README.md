# ⚙️ Execution Worker (`execution-worker`)

Фоновый headless-демон исполнения доменных операций в среде Windows.

---

## 📌 Зона ответственности

* **Отвечает за:**
  * Обработку очереди задач на исполнение из Redis Streams (`stream:execution_queue`) через Consumer Group (`execution_group`).
  * Прямое взаимодействие с инфраструктурой Windows (Active Directory, PowerShell, WinRM, WMI, SMB).
  * Выдачу сетевых доступов (группа `WLAN-WORKNET`), создание пользователей в AD.
  * Удаленную установку и диагностику принтеров через WinRM/CIM.
  * Режим Human-in-the-Loop (`confirm`) с блокирующим ожиданием решения оператора.
  * Отправку регулярного Heartbeat в Redis (`worker:health:win_daemon`).
  * Фиксацию сбойных задач в Dead-Letter Queue (DLQ, `stream:execution_failed`).

* **НЕ отвечает за:**
  * Хранение бизнес-логики триажа или шаблонов ответов (делегируется в Core API).
  * Прямой опрос очереди IntraService (делегируется в Poller).
  * Хранение учетных данных пользователей в открытом виде.

---

## 📡 Каналы связи и протоколы

| Канал / Протокол | Назначение | Контракт |
|---|---|---|
| **Redis Stream:** `stream:execution_queue` | Входная очередь задач от Core API | JSON-пейлоад: `{job_id, action, task_id, mode, payload}` |
| **Redis Stream:** `stream:execution_failed` | DLQ для аудита сбойных задач | JSON-пейлоад с ошибкой и трассировкой |
| **Redis Key:** `job:{id}:confirm` | Канал HITL-решений оператора | `brpop` решения: `approve` / `reject` |
| **Redis Key:** `worker:health:win_daemon` | Метка доступности демона (Heartbeat) | Значение `online` (TTL 25s) |
| **HTTP REST:** `http://core-api:8000/api/v1` | Запросы данных и закрытие тикетов | Заголовок `X-Bot-Api-Key` |
| **Active Directory / WinRM** | Корпоративный домен | PowerShell cmdlets, CIM over WinRM:5985 |

---

## 🔐 Ключевые инварианты

1. **Защита от слепого закрытия (Verified Execution Only):**
   Закрытие тикета или перевод в `Выполнена` отправляется в Core API только после фактической успешной проверки выполнения действия в инфраструктуре.
2. **Fail-Fast TCP Probe:**
   Перед тяжелыми вызовами WinRM/WMI сервис выполняет быструю TCP-проверку порта 5985 (таймаут 1.5 сек) для моментального отсечения недоступных хостов.
3. **Гарантированное подтверждение (At-Least-Once):**
   Каждая задача подтверждается через `XACK` только после завершения обработки либо после гарантированной записи в DLQ.

---

## 🚀 Запуск сервиса

### Требования:
- ОС: Windows (с доступом к корпоративному домену и правами на выполнение PowerShell).
- Python 3.11+, пакетный менеджер `uv`.

### Локальный запуск на целевом узле Windows:
```powershell
# Установка переменных окружения
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
$env:CORE_API_URL = "http://127.0.0.1:8000/api/v1"
$env:BOT_API_KEY = "<secret-api-key>"

# Запуск демона
uv run python execution-worker/worker.py
```
