# 🖥️ Desktop Companion (`desktop-companion`)

Лёгкий нативный Windows-клиент (tray-helper) на базе **Tauri 2**, дополняющий веб-панель `intra-web` локальными системными интеграциями рабочего места инженера первой линии.

---

## 📌 Зона ответственности

* **Отвечает за:**
  * Устранение рутинных переключений окон и вызовов внешних утилит инженера Helpdesk.
  * Безопасный запуск разрешенных локальных клиентов удаленного доступа: **LiteManager** (`romviewer.exe`), **DameWare** (`dwrcc.exe`), **RDP** (`mstsc.exe`).
  * Перехват и валидацию одноразовых deep links (`intralink://connect?...`), генерируемых Core API.
  * Локальные Windows-уведомления о требующих внимания заявках (HITL-согласования, инциденты).
  * Фокусное окно карточки заявки (SLA, заявитель, хост, быстрый возврат в `intra-web`).
  * Graceful Fallback: если Companion не запущен, веб-панель продолжает штатно работать (копирует команду подключения в буфер обмена).

* **НЕ отвечает за:**
  * Хранение доменных паролей или секретов учетных записей (все секреты изолированы в Core API Vault).
  * Прямое подключение к Redis или базам данных.
  * Выполнение произвольных команд PowerShell или системных shell-скриптов.
  * Инфраструктурные изменения в AD/сетевом контуре (делегируется строго в `execution-worker`).

---

## 📡 Архитектура и каналы связи

```text
intra-web (React 19) ──── Deep Link / HTTP ────> Desktop Companion (Tauri 2)
     │                                                     │
     │ HTTPS REST                                          ▼ Локальный запуск
     ▼                                             ┌─────────────────────────┐
Core API Gateway ── Redis Streams ──> Worker       │ romviewer / dwrcc/ mstsc│
                                                   └─────────────────────────┘
```

| Канал / Протокол | Назначение | Контракт |
|---|---|---|
| **Deep Link Protocol:** `intralink://connect` | Вызов запуска локального клиента из браузера | `intralink://connect?client={litemanager|dameware|rdp}&host={target}&token={ticket_token}` |
| **HTTPS REST:** Core API (`/api/v1/desktop/...`) | Проверка валидности разового токена запуска | Заголовок Bearer JWT / One-time grant |
| **Windows Process Execution:** (Rust Command API) | Запуск бинарных клиентов с жестко фиксированными аргументами | Strict Allowlist в `capabilities/default.json` |

---

## 🔐 Ключевые инварианты безопасности

1. **Strict Capability Allowlist:**
   Никакого динамического вызова `cmd.exe` или `powershell.exe`. Разрешены строго три исполняемых файла: `romviewer.exe`, `dwrcc.exe`, `mstsc.exe`. Любые аргументы валидируются на уровне Rust-бэкенда.
2. **Валидация целей подключения:**
   Имя хоста или IP-адрес проверяются регулярным выражением перед вызовом процесса. Символы инъекций команд (`;`, `&`, `|`, `` ` ``) моментально блокируют исполнение.
3. **Локальное хранение конфигурации:**
   Пути к установленным утилитам на ПК инженера хранятся исключительно в профиле Windows текущего пользователя (`%APPDATA%`).
4. **Non-blocking Pilot:**
   Сервис спроектирован как чистое расширение UX: его отсутствие или сбой ни при каких условиях не нарушают работу веб-интерфейса `intra-web`.

---

## 🛠️ Структура проекта

```text
desktop-companion/
├── src-tauri/             # Rust-бэкенд на базе Tauri 2
│   ├── src/
│   │   ├── lib.rs         # Регистрация плагинов, deep links и tray icon
│   │   └── main.rs        # Точка входа приложения
│   ├── capabilities/      # Декларативные разрешения безопасности (OS permissions)
│   ├── tauri.conf.json    # Конфигурация окна, бандлера и протоколов
│   └── Cargo.toml         # Зависимости Rust
└── ui/                    # Минималистичный React/HTML интерфейс настроек в трее
```

---

## 🚀 Запуск и сборка

### Требования к окружению:
* Rust 1.77+ (MSVC toolchain на Windows)
* Node.js 20+ и менеджер пакетов `npm` / `pnpm`
* Microsoft Visual Studio C++ Build Tools

### Локальная разработка:
```powershell
cd desktop-companion
npm install
npm run tauri dev
```

### Релизная сборка Windows инсталлятора / portable:
```powershell
npm run tauri build
```
Исполняемый файл будет собран в `desktop-companion/src-tauri/target/release/intralink-companion.exe`.
