# IntraBot-gemini: Project Context & Guidelines

This document provides foundational context and instructions for AI agents working on the IntraBot-gemini project.

## Project Overview

**IntraBot-gemini** is a Telegram bot designed to monitor and notify users about ticket updates in the **IntraService** helpdesk system. It provides real-time alerts for new tickets, status changes, and new comments.

### Core Functionality
- **User Authentication**: Secure Basic Auth integration with IntraService.
- **Real-time Monitoring**: Background polling for new tasks and updates.
- **Interactive Commands**: `/start`, `/help`, `/login`, and `/mytickets`.
- **Persistent Storage**: Local SQLite database for user credentials and polling state.

### Tech Stack
- **Language**: Python 3.10+
- **Framework**: [aiogram 3.x](https://docs.aiogram.dev/) (Asynchronous Telegram Bot API)
- **HTTP Client**: [aiohttp](https://docs.aiohttp.org/) (Asynchronous requests to IntraService API)
- **Database**: [aiosqlite](https://github.com/omnilib/aiosqlite) (Async SQLite wrapper)
- **Task Scheduling**: [asyncio.create_task](https://docs.python.org/3/library/asyncio-task.html) for background polling.
- **Config**: `python-dotenv` for environment variables.

---

## Directory Structure

- `database/db.py`: Database schema and async CRUD operations for users and their state.
- `handlers/`: Telegram event handlers grouped by feature.
  - `auth.py`: FSM (Finite State Machine) for user login.
  - `tickets.py`: Logic for viewing and listing tickets.
  - `start_help.py`: Basic bot commands.
- `services/`: Core business logic and external integrations.
  - `api.py`: IntraService API wrapper.
  - `scheduler.py`: Background polling loop implementation.
- `config.py`: Centralized configuration management.
- `main.py`: Application entry point and initialization.
- `docs/`: Comprehensive IntraService API documentation (split into multiple parts).
  - **CRITICAL**: Refer to `docs/GEMINI.md` for specific IntraService API usage rules.

---

## Building and Running

### Prerequisites
- Python 3.10 or higher.
- A Telegram Bot Token (from @BotFather).
- Access to an IntraService instance API.

### Setup
1. **Environment**: Create `.env` from `.env.example`.
   ```env
   BOT_TOKEN=your_telegram_bot_token
   INTRAService_URL=https://your-domain.intraservice.ru/api/
   POLLING_INTERVAL=60
   ```
2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Initialize Database**: The database is automatically initialized on the first run via `main.py`.

### Execution
```bash
python main.py
```

---

## Development Conventions

### Coding Style
- **Asynchronous First**: All I/O operations (API calls, DB queries) MUST be asynchronous.
- **Modular Handlers**: Keep Telegram handlers focused on UI/UX; delegate business logic to `services/`.
- **FSM Usage**: Use `aiogram`'s FSM for multi-step interactions (like the `/login` flow).
- **Error Handling**: Implement robust error handling for API timeouts and invalid credentials.

### API Integration
- **Auth**: Store credentials as Base64 strings in the database.
- **SSL**: SSL verification is currently disabled (`SSL_VERIFY = False` in `services/api.py`) for compatibility with internal domains. This should be configurable in production.
- **Polling State**: Track `last_task_id` and `last_check_time` per user to avoid redundant notifications.

### Documentation Reference
- **IntraService API**: Use `docs/IntraService_API_Index.md` to locate specific endpoint details.
- **Schemas**: See `docs/Schemas.md` for JSON request/response structures.

---

## TODOs & Future Improvements
- [ ] **Security**: Implement encryption for stored passwords in the SQLite database.
- [ ] **Scalability**: Replace `asyncio.create_task` polling with a more robust task queue (e.g., Celery or APScheduler) for many users.
- [ ] **Logging**: Implement structured logging and error reporting (e.g., Sentry).
- [ ] **Attachments**: Add support for viewing/uploading files to tickets.
