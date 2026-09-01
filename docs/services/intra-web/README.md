# Intra Web (`intra-web`) — Admin SPA Panel

Современная веб-панель управления, мониторинга очередей и AI-автоматизации Helpdesk для инженеров и администраторов на базе **React 19**, **Vite**, **Tailwind CSS v4** и `vite-plugin-singlefile`.

---

## 📌 Назначение
1. **Мониторинг очереди заявок (Queue Dashboard):** Просмотр активных инцидентов 1-й линии, фильтрация по статусам, сервисам и аналитические виджеты.
2. **Инспектор заявки (Ticket Inspector):** Просмотр истории переписки, вложений (скриншотов), сетевого статуса заявителя и формы быстрого ответа/закрытия.
3. **Управление AI и RAG:** Настройка параметров автоответов, квот сбора базы знаний, запуск переиндексации и прослушивание логов в реальном времени через Server-Sent Events (SSE).
4. **Установка оргтехники:** Ручной запуск и отслеживание статуса установки драйверов на рабочих станциях.
5. **Управление учетными данными:** Настройка доменных реквизитов WinRM/SMB.

---

## 🏗 Архитектура сборки и раздачи

* **Стек:** React 19, TypeScript, Tailwind CSS v4 (`@tailwindcss/vite`), Lucide Icons, Vite 6.
* **Single-File Bundle:** При сборке (`npm run build`) плагин `vite-plugin-singlefile` компилирует всё приложение (JS + CSS + HTML) в единый автономный файл `core-api/app/static/admin/index.html`.
* **Multi-stage Docker Build:** В [`core-api/Dockerfile`](../../../core-api/Dockerfile) на первом этапе (`frontend-builder`) запускается `npm ci && npm run build` внутри `intra-web/`, после чего скомпилированный `index.html` копируется в финальный Python-образ.
* **Раздача:** Раздаётся напрямую веб-сервером Core API по маршруту `/admin`.
* **Безопасность:** Защита через HTTP-only JWT-куку `admin_session`, получаемую через `/admin/api/login`.

---

## 🚀 Разработка интерфейса

```bash
cd intra-web
npm install
npm run dev
```

Dev-сервер запускается на `http://localhost:5173` с автоматическим проксированием запросов `/admin/api` к работающему Core API (`http://localhost:8000`).

### Сборка:
```bash
npm run build
```
Скомпилированный файл автоматически сохраняется в `core-api/app/static/admin/index.html`.
