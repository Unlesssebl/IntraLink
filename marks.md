
###
Logs
###

1. [РЕШЕНО] Жесткий спам intralink/litellm	INFO:     172.19.0.5:47640 - "POST /v1/embeddings HTTP/1.1" 200 OK
   - **Причина:** фоновый поллинг `App.tsx` каждые 15 секунд вызывал `/api/v1/triage/batch` на пачку заявок. В цикле триажа для каждой стандартной заявки без кэша вызывался `get_embedding_vector`, отправлявший сотни HTTP POST в LiteLLM.
   - **Решение:**
     1. В [`rag.py`](file:///C:/Users/belikov.a/Desktop/%D0%90%D0%BA%D1%82%D1%8B,%20%D0%B4%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D1%8B/Work/%21Projects/intralink/core-api/app/services/rag.py) реализован двухуровневый кэш векторов: быстрый in-memory LRU (`0ms`) + персистентный Redis (`rag:emb:...`, TTL 7 дней, `1ms`). Повторные запросы для того же текста не обращаются к LiteLLM.
     2. В `/api/v1/triage/batch` (`prepare_triage_batch`) добавлен флаг `include_rag=False` по умолчанию. При фоновом опросе очереди отрабатывает мгновенный Rule Engine (10-20ms) без вызовов RAG.
     3. Семантический RAG-поиск вызывается точечно при открытии конкретного тикета инженером в инспекторе (`/api/v1/triage/tasks/{task_id}`).