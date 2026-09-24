# 🚀 IntraLink v2: Комплексный план реализации системы

> **Актуальная спецификация:** [v2-automation-implementation-plan.md](file:///docs/plans/v2-automation-implementation-plan.md)  
> **Архитектурный манифест:** [v2-architecture-blueprint.md](file:///docs/architecture/v2-architecture-blueprint.md)  
> **Конвейер автопилота:** [v2-automation-pipeline.md](file:///docs/architecture/v2-automation-pipeline.md)

---

## 🧭 Сводка спринтов реализации автономного конвейера v2

| Спринт | Фокус работ | Ключевые компоненты | Статус |
| :---: | :--- | :--- | :---: |
| **Спринт 1** | **Taskiq Runtime & Очереди** | `worker/src/broker.py`, `worker/src/main.py`, Docker Taskiq runtime | ✅ Завершен |
| **Спринт 2** | **Фоновый синк Базы знаний** | `sync_kb.py`, PII-очистка, cron 02:00, FastEmbed батчи в pgvector | ✅ Завершен |
| **Спринт 3** | **Ingestion & Пульс 30с** | Поллер `filterid=984` + `ChangedMoreThan`, двойной Watermark (Redis+Postgres) | ⏳ Готов к старту |

| **Спринт 4** | **Шлюз релевантности & Сущности** | Нормализация ПК/IP/Учеток, фоновый триаж, авто-отмена нецелевых тикетов | 📋 Запланирован |
| **Спринт 5** | **Сценарии Core-3 & Диалог** | Принтеры, AD, RAG-консультации, диалог с заявителем, Circuit Breaker | 📋 Запланирован |

Подробные критерии приемки (DoD) и детальный список задач зафиксированы в [docs/plans/v2-automation-implementation-plan.md](file:///docs/plans/v2-automation-implementation-plan.md).
