# Intra Web (`intra-web`) — Admin SPA Panel

Современная веб-панель управления, мониторинга очередей и AI-автоматизации Helpdesk для инженеров и администраторов на базе **React 19**, **Vite**, **Tailwind CSS v4** и `vite-plugin-singlefile`.

---

## 📌 Назначение

Web-панель **IntraLink** разделена на два контура:
1. **Рабочее место инженера техподдержки (Helpdesk Engineer Cockpit):**
   - Оперативный мониторинг и триаж очереди 1-й линии (Filter 984).
   - Инспектор заявки с сетевой экспресс-диагностикой хоста (Ping, SMB:445, WinRM:5985).
   - 1-Click смарт-действия (Редиректы, отмена дубликатов с привязкой к Master Ticket, каб. 112, Wi-Fi в AD).
   - Пакетная обработка заявок (Bulk Triage) с автосписанием трудозатрат.
   - Просмотр вложений, скриншотов и истории переписки (`tasklifetime`).
2. **Административный раздел (`/admin`):**
   - Подробная спецификация вынесена в отдельный документ: [`admin_specification.md`](admin_specification.md).

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
