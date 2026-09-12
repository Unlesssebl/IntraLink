# Технический план этапа 4: AI, fallback и объяснимость

Дата: 2026-09-10. Статус: готов к реализации после архитектурной ревизии; Fast Fix по лимитам RAG и облачных токенов уже реализован и верифицирован отдельно.

Связанные материалы: [roadmap](ticket-scenario-quality-roadmap.md), [план этапа 2](ticket-scenario-quality-stage-2-plan.md), [план этапа 3](ticket-scenario-quality-stage-3-plan.md), [концепция адаптивного RAG и тональности](model-adaptive-rag-and-tone-orchestration.md), [сравнение replay](../../core-api/tests/fixtures/ticket_quality/replay/comparison.md), [готовность сценарного ядра](archive/scenario-core-readiness.md).

---

## 1. Результат и границы выпуска

### 1.1. Главная цель

Сделать AI-синтез управляемым presentation-слоем над уже скомпилированным сценарным решением: контекст соответствует фактически использованной модели, деградация не скрывается, смена тональности не меняет факты, сценарий, outcome, политику или допуск к действию, а оператор видит источник текста и ограничения решения.

Этап должен:

1. Подключить RAG и AI к каноническому durable-пути явного анализа, не возвращая генерацию в read-only GET.
2. Применять `Capacity-Aware Context Budgeting` к фактически выбранному backend, включая локальный fallback после отказа облака.
3. Сохранять один канонический ответ по умолчанию и формировать варианты `concise`, `detailed` и `regulatory` только по явному запросу оператора.
4. Отделить перегенерацию текста от `/analyze` и `/reanalyze`: тональность является версией response artifact, а не новой версией решения.
5. Сохранять проверяемую provenance ответа: источник, фактический backend/model, контур, профиль контекста, использованные RAG-ссылки и нормализованную причину fallback.
6. Показывать отдельно оценку маршрутизации сценария, полноту обязательных фактов, готовность к применению и состояние AI. Ни один из этих индикаторов не подменяет другой.
7. Сохранить fail-closed правила этапов 2–3: RAG/LLM не разрешают инфраструктурное действие, не ослабляют policy gates и не подтверждают выполнение без evidence.

### 1.2. Подтверждённая исходная точка

Fast Fix уже добавил в текущий checkout:

- `AI_OLLAMA_MAX_RAG_MATCHES=1`, `AI_CLOUD_MAX_RAG_MATCHES=3` и `AI_CLOUD_MAX_TOKENS=1536` с валидацией в `app/config.py` и примером в `.env.example`;
- срез валидных RAG-прецедентов по вычисленному контуру/настройке провайдера в legacy-функции `synthesize_triage_resolution()`;
- тест локального лимита 1 и облачного лимита 3;
- стабильный alias `intralink-chat`, два cloud deployment под ним и `least-busy`/cooldown в `litellm_config.yaml`;
- клиентскую защиту от обрыва облачного ответа из-за слишком малого `max_tokens`.

Это не считается завершённым этапом 4 по следующим причинам:

- канонический `POST /api/v2/triage/tasks/{task_id}/analyze` получает read-only snapshot и вызывает `ScenarioDecisionService` без `kb_matches`; Fast Fix действует в основном в compatibility/legacy-пути;
- `ScenarioDecisionService._generate_low_risk_response()` теряет сведения о фактически ответившей модели и причине деградации;
- `DecisionResponse` хранит только `mode/state/violations`, поэтому `template`, отказ AI и отклонение ответа guard нельзя объяснить однозначно;
- локальный fallback настроен одновременно в LiteLLM и в Core API. Первый слой возвращает Core API только текст и скрывает реальный backend, а второй не может пересобрать уже подготовленный cloud prompt с локальным RAG-бюджетом;
- текущий AI cache не включает все смысловые параметры генерации, в частности итоговый токенный бюджет и ревизию prompt policy;
- API тональности и отдельный UI-контур в `UnifiedActionDock` отсутствуют.

Перед реализацией проверить эти утверждения на актуальной ветке. Статус Fast Fix означает наличие и прохождение целевых тестов в checkout, но не доказывает, что изменения развернуты в production.

### 1.3. In scope

- Совместимое расширение `DecisionResponse` данными provenance.
- Новый `ResponseVariantService` и endpoint генерации варианта для конкретной версии решения.
- Durable-хранилище вариантов ответа и Alembic-миграция.
- Канонический сбор RAG-контекста для явного анализа с безопасной деградацией при недоступности pgvector, embeddings или reranker.
- Один владелец cross-backend fallback и повторная сборка prompt под локальный профиль.
- Тональности `default`, `concise`, `detailed`, `regulatory` с детерминированными ограничениями.
- Повторное применение `response_guard`/`TruthfulnessGuard` к каждому варианту и к фактическому `apply_triage` payload.
- Компактная AI/provenance-индикация и раздельные показатели качества в Инспекторе.
- Контрактные, unit, integration, UI, failure-injection, replay и миграционные тесты.
- Shadow/HITL rollout. Автоматическая отправка AI-текста не включается.

### 1.4. Out of scope

- Изменение scenario routing, FactBag, `ExecutionPlan`, outcome или policy по запросу тональности.
- Генерация нескольких ответов одновременно и ранжирование вариантов LLM-моделью.
- Использование LLM/RAG confidence как вероятности правильности или допуска к действию.
- Автоматическая публикация комментария в IntraService после генерации.
- Передача внутренних сводок, raw worker payloads, секретов либо полного prompt в UI/метрики.
- Требование к заявителю самостоятельно выполнять переустановку ОС, правку реестра, привилегированные PowerShell-команды или иные инженерные действия. Режим `detailed` не отменяет разграничение ответственности этапа 3.
- Полный контур `decision_feedback`/`decision_applications` и аналитика правок оператора, запланированные на этапе 5. Этап 4 сохраняет только provenance, необходимую для объяснения происхождения выбранного текста.
- Перевод сценариев или автопилота в production `active`.

---

## 2. Архитектурные инварианты

### 2.1. Тональность не является повторным анализом

`DecisionEnvelope` остаётся неизменяемым снимком фактов, маршрутизации, outcome, policy, плана и gates. Запрос другого тона:

- обязан содержать `decision_id` и `decision_version`;
- использует сохранённые `facts_summary`, policy, evidence refs и plan phase этого решения;
- не вызывает collectors, router, compiler и не повышает `decision_version`;
- не меняет `status_id`, `expenses`, `requires_approval`, action parameters или готовность;
- отклоняется с `409 decision_stale`, если решение уже не является текущим либо fingerprint заявки изменился.

`/reanalyze` остаётся отдельной явной операцией для изменения фактов и решения. Параметр `tone` не добавляется в `/apply`: применение принимает фактический текст, текущую версию решения и необязательную ссылку на response variant.

### 2.2. Один ответ и одна семантика

По умолчанию в `DecisionEnvelope.response` хранится ровно один безопасный канонический текст. Варианты по тону создаются последовательно по запросу оператора; backend не возвращает массив альтернатив.

Тон может менять длину и подачу, но не обязательные утверждения policy. Для одного `decision_id/version` все допустимые варианты должны сохранять:

- тот же outcome и фазу обслуживания;
- тот же public/internal channel;
- тот же набор разрешённых evidence refs;
- запрет неподтверждённых claims;
- запрет новых идентификаторов, адресов, команд и действий, отсутствующих в разрешённом контексте.

### 2.3. Бюджет определяется фактическим backend

Система сначала получает DLP route, затем строит два контекстных профиля из одного ранжированного набора RAG-кандидатов:

- `local`: первые `AI_OLLAMA_MAX_RAG_MATCHES`, по умолчанию 1;
- `cloud`: первые `AI_CLOUD_MAX_RAG_MATCHES`, по умолчанию 3.

RED никогда не отправляется в cloud. YELLOW/GREEN сначала используют cloud-профиль. Если cloud исчерпан по timeout/rate-limit/unavailable, локальный fallback получает заново собранный local-профиль, а не исходный cloud prompt. Уменьшение списка только перед первой попыткой недостаточно: фактически ответившая Ollama не должна видеть cloud-sized context.

Лимит является верхней границей, а не требованием заполнить prompt. Кандидат допускается только после source-quality gate и versioned rerank threshold. Отсутствие подходящих прецедентов даёт `rag_used_count=0`, а не подстановку слабого совпадения.

### 2.4. Один владелец cross-backend fallback

LiteLLM отвечает за балансировку и cooldown cloud deployment одного alias `intralink-chat`. Core API отвечает за переход cloud → local, потому что только он может:

- пересобрать prompt с локальным бюджетом;
- применить DLP-правила и разные token limits;
- зафиксировать каждую попытку и точную причину fallback;
- проверить результат общим response guard.

После включения нового контура удалить `intralink-chat -> intralink-local` из `router_settings.fallbacks`. Прямой RED-route и локальный fallback выполняются через Ollama из Core API. Одновременный скрытый fallback на обоих уровнях запрещён.

### 2.5. Детерминированный fallback является успешной деградацией, а не AI-успехом

Безопасный template может сохранить `can_send_response=true`, если policy и guard разрешают текст. При этом provenance обязана показать `source=template` или `source=fallback_template` и reason code. UI не должен превращать такой результат в общую ошибку анализа и не должен показывать, что ответ сформирован моделью.

`regulatory` всегда рендерится из активной versioned policy/template без LLM. Его источник `template` и `fallback_used=false`; это штатный режим, а не деградация.

### 2.6. Объяснимость не ослабляет безопасность

В UI и API отдельно представлены:

1. `routing.selected_score` — оценка выбора сценария, не вероятность.
2. Полнота обязательных фактов — отношение valid requirements к общему числу и список missing/conflicting/stale.
3. `gates` — фактическая готовность отправить ответ или выполнить действие.
4. `response.provenance` — происхождение текста и состояние AI/RAG.

Ни высокий routing score, ни успешный AI, ни наличие RAG не меняют gates. RAG/LLM никогда не устанавливают `can_authorize_action=true` и не создают evidence выполнения.

### 2.7. Матрица принятых архитектурных решений

| Вопрос | Минимальный вариант | Выбранное решение | Причина выбора |
|---|---|---|---|
| Смена тона | Query-параметр в `/reanalyze` | Отдельный version-bound `ResponseVariantService` | Не пересчитывает факты/решение, не создаёт ложную новую версию и сохраняет правку оператора |
| Cross-backend fallback | Оставить fallback внутри LiteLLM | Cloud rotation в LiteLLM, cloud → local в Core API | Core API может пересобрать local prompt, применить DLP/guard и записать actual backend |
| Provenance вариантов | Только transient UI metadata | Append-only variant table плюс provenance в default response | Объяснимость переживает refresh/restart и может быть связана с фактическим apply |
| Detailed tone | Свободная генерация инструкции | Редактура policy и allowlisted public steps | Не перекладывает привилегированные инженерные действия на заявителя |
| Ошибка RAG/AI | Сделать весь анализ failed | Сохранить решение и безопасный template с degradation codes | Внешняя необязательная зависимость не должна скрывать детерминированное решение |

---

## 3. Целевая архитектура генерации

### 3.1. Канонический поток явного анализа

`run_explicit_analysis()` сохраняет существующие lease/fencing и проверку изменения заявки. Внутри одной попытки порядок становится следующим:

1. Получить read-only snapshot заявки и истории.
2. Собрать детерминированные facts/diagnostics.
3. Вычислить DLP circuit по исходному контексту.
4. Выполнить hybrid RAG retrieval и Cross-Encoder rerank с bounded timeout; сохранить только безопасные ссылки и quality metadata.
5. Повторно проверить fingerprint заявки и владение analysis lease.
6. Передать один и тот же ранжированный `kb_matches` в `ScenarioDecisionService` и response orchestration.
7. Скомпилировать immutable `DecisionEnvelope`, применить guard и записать решение.

RAG outage не превращается в исчезновение заявки или `analysis_failed`. Движок продолжает работу с `kb_matches=[]`, а response provenance получает нормализованный `rag_unavailable`/`reranker_unavailable`. Для `rag_consultation` отсутствие пригодного прецедента означает обычный deterministic consultation/manual fallback, а не выдуманное решение.

Обычные `GET /batch` и `GET /tasks/{task_id}` остаются read-only и не запускают retrieval или inference. Server-side `/analyze-batch` использует тот же bounded pipeline и существующий лимит `TRIAGE_ANALYSIS_MAX_CONCURRENCY`.

### 3.2. ResponseContextBuilder

Добавить сервис, который принимает только скомпилированные данные решения:

```python
class ResponseContext(StrictModel):
    decision_id: str
    decision_version: int
    scenario_key: str
    scenario_version: int
    outcome_kind: str
    plan_phase: str | None
    policy_text: str
    allowed_evidence_refs: list[str]
    confirmed_facts: dict[str, Any]
    public_plan_steps: list[dict[str, str]]
    rag_candidates: list[RagReference]
```

Builder не получает `internal_summary`, raw comments, secrets, полный FactBag или необработанные диагностические payloads. Значения facts проходят существующие sensitivity/redaction и включаются только при `state=valid`. Шаги плана преобразуются в публичные нейтральные формулировки; `executor=engineer/windows`, privileged action и unsupported capability не становятся инструкцией заявителю.

RAG-reference содержит стабильную ссылку на прецедент, versioned score/profile и разрешённый фрагмент решения. В persistent provenance сохраняются идентификаторы/оценки и digest, но не полный prompt и не дублированный текст прецедента.

### 3.3. Provider-aware prompt variants

Расширить `RoutedInferenceRequest` совместимым optional-блоком prompt variants. Старые callers могут продолжать передавать `prompt/system_prompt`; новый response path передаёт оба профиля:

```python
class PromptVariant(StrictModel):
    profile: Literal["local", "cloud"]
    prompt: str
    system_prompt: str
    rag_refs: list[str]
    max_tokens: int

class InferencePurpose(str, Enum):
    RESPONSE_DEFAULT = "response_default"
    RESPONSE_CONCISE = "response_concise"
    RESPONSE_DETAILED = "response_detailed"
```

AI Hub выбирает variant после DLP route. При cloud transport failure он делает не внутренний повтор того же prompt, а отдельную Ollama attempt с local variant и остатком общего deadline.

`RoutedInferenceResponse` аддитивно получает:

- `requested_backend` и `actual_backend`;
- фактические `model_alias` и `resolved_model`, если провайдер их вернул;
- `fallback_used` и `fallback_reason_code`;
- `context_profile`, `rag_refs`, `prompt_revision`;
- список sanitised `attempts` с backend, outcome, reason code и duration;
- cache hit, token usage при наличии и total duration.

Имена API-ключей, provider error body, prompt и исходные PII в metadata не сохраняются.

### 3.4. Общий deadline и классификация отказов

Последовательный cloud → local fallback ограничивается единым server-side deadline. Значение выносится в Settings и калибруется по shadow-наблюдениям; отдельная попытка не может продлить общий запрос бесконечно. Отмена HTTP-request должна отменять незапущенную следующую attempt и освобождать single-flight lease.

Минимальный стабильный набор reason codes:

- `cloud_timeout`;
- `cloud_rate_limited`;
- `cloud_unavailable`;
- `local_timeout`;
- `local_unavailable`;
- `dlp_route_failed`;
- `rag_unavailable`;
- `reranker_unavailable`;
- `invalid_schema`;
- `response_guard_rejected`;
- `regulatory_template_unavailable`;
- `all_providers_failed`.

Неизвестное исключение маппится в `provider_error`, журналируется с correlation ID и не передаёт stack/error body клиенту.

---

## 4. Контракты, persistence и кэш

### 4.1. Response provenance

`DecisionResponse` расширяется обратно совместимым optional-полем. Исторические envelope без provenance продолжают валидироваться и показывают «Источник не зафиксирован».

```python
class ResponseProvenance(StrictModel):
    schema_version: Literal[1] = 1
    requested_tone: Literal["default", "concise", "detailed", "regulatory"]
    effective_tone: Literal["default", "concise", "detailed", "regulatory"]
    source: Literal["llm", "template", "fallback_template"]
    circuit: Literal["red", "yellow", "green"] | None = None
    requested_backend: Literal["litellm", "ollama", "deterministic"]
    actual_backend: Literal["litellm", "ollama", "deterministic"]
    model_alias: str | None = None
    resolved_model: str | None = None
    context_profile: Literal["none", "local", "cloud"] = "none"
    rag_candidate_count: int = 0
    rag_used_count: int = 0
    rag_refs: list[str] = Field(default_factory=list)
    prompt_revision: str
    fallback_used: bool = False
    fallback_reason_code: str | None = None
    degradation_codes: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    duration_ms: float | None = None
```

`source=fallback_template` означает, что попытка AI была, но итоговым разрешённым текстом стал template. Если AI не вызывался по policy, используется `source=template`. `fallback_reason_code` описывает смену источника/backend, а независимые ограничения вроде `rag_unavailable` записываются в `degradation_codes`. Модель не выводится из строки с суффиксом `(fallback)`; backend и fallback являются отдельными typed-полями.

### 4.2. Durable response variants

Alembic revision после `20260910_0013` добавляет `decision_response_variants`:

| Поле | Назначение |
|---|---|
| `id UUID` | Идентификатор варианта |
| `decision_id UUID FK` | Точная версия решения-источника |
| `tone VARCHAR(16)` | `default/concise/detailed/regulatory` |
| `channel VARCHAR(16)` | На этапе 4 только `reply` |
| `state VARCHAR(16)` | Только выданный оператору `valid/fallback`; отклонённая model attempt остаётся в provenance итогового fallback |
| `text TEXT` | Проверенный публичный текст |
| `content_hash VARCHAR(64)` | Проверка неизменности и связи с apply |
| `provenance_json JSONB` | Typed provenance без prompt/PII |
| `request_fingerprint VARCHAR(64)` | Идемпотентность одинакового запроса |
| `created_by VARCHAR(100)` | Оператор или system actor |
| `created_at TIMESTAMPTZ` | Время создания |

Добавить FK/index по `decision_id`, индекс `created_at` и unique constraint на `request_fingerprint`. Для `regenerate=true` сервер добавляет новый generation nonce к fingerprint; обычные повторы остаются идемпотентными. Таблица append-only; смена тона не перезаписывает envelope и ранее сгенерированные варианты. Downgrade удаляет только новую таблицу и не изменяет решения/команды.

Default-текст остаётся внутри envelope как каноническая часть решения. Вариант из отдельной таблицы является presentation artifact. Если оператор применяет вариант, `ApplyTriageRequest` принимает необязательный `response_variant_id`; Command Service проверяет принадлежность текущему `decision_id`, повторно валидирует фактический `comment` и сохраняет в command/application metadata:

- variant ID и tone;
- исходный `content_hash`;
- `operator_edited=true`, если отправляемый текст отличается;
- hash итогового текста.

Правка оператором разрешена, если новый текст проходит guard. Система не должна приписывать отредактированный текст модели дословно.

### 4.3. API генерации варианта

Добавить endpoint:

```text
POST /api/v2/triage/tasks/{task_id}/response-variants
```

Request:

```json
{
  "decision_id": "uuid",
  "decision_version": 4,
  "tone": "concise",
  "regenerate": false
}
```

Response содержит `variant_id`, identity решения, `tone`, проверенный `DecisionResponse` и `ResponseProvenance`. Endpoint:

1. требует `triage:mutate` и trusted origin;
2. вызывает `DecisionJournalService.require_current()`;
3. проверяет ticket fingerprint перед внешним inference;
4. не вызывает анализ и не изменяет решение;
5. применяет single-flight/idempotency для повторного клика;
6. возвращает существующий вариант при том же request fingerprint, если `regenerate=false`;
7. rate-limits явную перегенерацию;
8. при stale возвращает 409, при недоступном regulatory template — безопасную typed-ошибку без затирания текущего черновика.

`regenerate=true` обходит только response-result cache. Он не обходит policy, DLP, RAG quality gate или response guard.

### 4.4. Изоляция кэша

Redis остаётся ускорителем, но не source of truth. Business cache key включает:

```text
ai:response-variant:v{prompt_revision}:{decision_id}:{decision_version}:
{tone}:{channel}:{policy_key}:{policy_version}:{context_profile}:{rag_digest}
```

Provider-attempt cache дополнительно включает фактический prompt digest, schema digest, model alias, backend, max tokens, temperature и DLP circuit. Cloud/local attempts имеют разные ключи. Изменение policy/template, prompt revision, RAG corpus revision, решения или тона автоматически создаёт другой ключ; удаление через `KEYS ai:resolution:{task_id}:*` не требуется.

Cache payload содержит текст вместе с provenance. Cache hit без metadata запрещён: иначе UI показал бы неизвестный backend. TTL конфигурируется; потеря Redis приводит к обычной генерации или template fallback, но не к ошибке решения.

---

## 5. Тональности и защита содержания

### 5.1. Матрица режимов

| Tone | Источник и форма | Обязательные ограничения | Поведение при отказе |
|---|---|---|---|
| `default` | Один канонический ответ policy, при low-risk допустима guarded LLM-редактура | Сохраняет фазу и смысл policy; 2–4 предложения; без новых фактов | Активный template |
| `concise` | Guarded LLM-редактура policy и текущего next step | 1–2 предложения; без пропуска обязательного уточнения/предупреждения | Краткий детерминированный template/compression |
| `detailed` | Guarded LLM-структурирование policy и публично безопасных шагов | 3–5 пунктов; только supplied facts/instruction IDs; инженерные действия не перекладываются на заявителя | Исходный policy template с причиной деградации |
| `regulatory` | Только active versioned `ResponseTemplate`/policy | Точное детерминированное форматирование, без LLM и RAG-синтеза | Typed unavailable; текущий draft сохраняется |

`effective_tone` отличается от requested только при безопасном documented fallback. Например, если `detailed` невозможно сформировать без выдумывания шагов, итоговый policy template получает `effective_tone=default`, `source=fallback_template`, `fallback_reason_code=response_guard_rejected`.

### 5.2. Detailed не является генератором произвольных инструкций

Модель получает только:

- active policy text и target phase;
- подтверждённые public facts;
- публично допустимые названия шагов текущего `ExecutionPlan`;
- typed instruction IDs, если они определены scenario/policy;
- allowed evidence refs.

Пути реестра, PowerShell/cmd, сетевые адреса и действия с правами допускаются только если они уже присутствуют в версионированном разрешённом policy/instruction source и предназначены заявителю. Модель не может сама превратить внутренний шаг `executor=windows/engineer` в пользовательскую инструкцию.

### 5.3. Structural и lexical guard

Каждый вариант проходит существующие проверки этапа 3 и дополнительные tone validators:

- `concise`: не более двух предложений после устойчивой сегментации; обязательный вопрос clarification не потерян;
- `detailed`: 3–5 структурированных пунктов либо безопасный fallback; все instruction IDs разрешены;
- `regulatory`: content hash совпадает с результатом deterministic template render;
- любой tone: нет prompt leakage, placeholder, неизвестного PC/IP, unsupported completion/running claim, внутренней сводки или evidence ref вне allowlist;
- status 29 и фазовые claims повторно проверяются на command boundary по фактическому comment.

LLM schema содержит `text`, `used_evidence_refs` и `used_instruction_ids`. Свободный JSON/dict без strict validation не принимается. Ошибка schema либо guard не проходит в UI как AI-текст; оператор получает безопасный template и объяснимую причину.

### 5.4. Policy eligibility

AI-редактура разрешается только для низкорискового публичного текста. Для RED/регламентных credential flows, отмены, юридически значимых формулировок и policy с запретом генерации используется template независимо от выбранного tone. UI показывает недоступные тона disabled с серверным reason code, а не предлагает действие, которое backend затем молча интерпретирует иначе.

---

## 6. Fallback, health и объяснимость оператора

### 6.1. Матрица деградации

| Сбой | Решение/сценарий | Публичный ответ | Provenance/UI |
|---|---|---|---|
| pgvector/embeddings недоступны | Продолжается без RAG, если сценарий детерминирован | Policy/template или AI без прецедентов | `rag_unavailable`, 0 used refs |
| Cross-Encoder недоступен | Непроверенные кандидаты не входят в prompt | Policy/template | `reranker_unavailable` |
| Один cloud key получил 429 | LiteLLM пробует другой cloud deployment | Без изменения семантики | Не раскрывать key; attempt остаётся cloud |
| Cloud alias исчерпан/timeout | Core API пересобирает local prompt | Ollama при успешном guard | `actual_backend=ollama`, local profile, reason code |
| Ollama также недоступна | Решение сохраняется | Active policy template | `source=fallback_template`, `all_providers_failed` |
| DLP route сломан | Cloud запрещён | Только безопасный template | `dlp_route_failed` |
| LLM вернул invalid JSON/claim | Ответ модели отклонён | Active policy template | `invalid_schema`/`response_guard_rejected` |
| Redis недоступен | Без потери correctness | Генерация без cache либо template по deadline | `cache_hit=false`, отдельная health degradation |

Ошибка AI не меняет успешное состояние безопасного сценарного анализа на `system_error`. `system_error` используется для невозможности сформировать само решение; response degradation хранится отдельно.

### 6.2. Проверка сервисов

Разделить readiness приложения и состояние AI-зависимостей:

- PostgreSQL/обязательные миграции остаются частью readiness Core API;
- LiteLLM, Ollama, embeddings и reranker отображаются как subsystem health и могут быть degraded;
- проверка LiteLLM выполняется через опубликованный alias `intralink-chat`, а не прямой provider model;
- отдельно проверяются direct Ollama completion и BGE-M3 dimension 1024;
- health-check не отправляет рабочие данные и не пишет response variant;
- UI не опрашивает провайдеры напрямую, а использует sanitised серверный статус/последнюю provenance.

### 6.3. UI Инспектора

В `UnifiedActionDock` добавить отдельную split-кнопку «AI Ответ» рядом с редактором, не смешивая её с основной кнопкой применения заявки. Меню содержит иконки из существующего набора без текстовых emoji:

- «Краткий статус»;
- «Подробно»;
- «По регламенту».

Поведение:

- запрос доступен только для current/non-stale `decision_id/version` и public reply mode;
- во время запроса блокируется повторный клик по этому tone, но textarea не блокируется;
- пустой/неизменённый draft может быть заменён автоматически;
- непустой или отредактированный draft никогда не затирается: используется существующий паттерн «Применить новый / Оставить мой»;
- public и internal drafts остаются раздельными; `internal_summary` не передаётся endpoint;
- смена заявки, версии решения или reply mode отменяет устаревший UI-request и не применяет поздний response;
- выбранный вариант сохраняется в session draft вместе с `variant_id`; ручная правка помечает его как edited;
- ошибка/stale сохраняет текущий текст и предлагает обновить анализ только когда это действительно требуется.

Рядом с кнопкой показывается компактный badge последнего фактического источника: `LiteLLM`, `Ollama`, `Регламент` или `Fallback`. Подробности раскрываются по tooltip/details: model, circuit, использованный профиль `1/3 RAG`, cache hit и нормализованная причина деградации. Полные prompt, прецеденты и PII не отображаются.

### 6.4. Раздельные индикаторы качества

В `UnifiedDecisionPanel` операторская сводка показывает четыре независимых значения:

- сценарий: score и ambiguity/runner-up;
- факты: `valid_required / total_required`, missing/conflicting/stale;
- готовность: send/action gates и blocked reasons;
- ответ: tone, source/backend и degradation.

`confidence` сохраняет текущее значение для совместимости, но подписывается как «оценка выбора», пока не выполнена калибровка. Процентная вероятность и объединённый «AI confidence» не показываются. Детальные RAG refs и attempts остаются в сворачиваемом техническом блоке.

---

## 7. Последовательность реализации

### 4A. Контракты и канонический RAG path

Компоненты: `shared/domain/scenario.py`, `app/services/scenario_decision.py`, `app/routers/triage.py`, `app/services/rag.py`, `intra-web/src/lib/types.ts`.

1. Добавить optional `ResponseProvenance` с backward-compatible parser.
2. Вынести `ResponseContextBuilder` и source-quality gate; не использовать legacy `synthesize_triage_resolution()` как второй SSOT.
3. Встроить bounded RAG retrieval в explicit analysis между snapshot и повторной fingerprint/lease-проверкой.
4. Передавать один ranked set в scenario routing и response orchestration; фиксировать candidate/used counts и деградацию.
5. Сохранить GET read-only и общий server-side bounded batch.

### 4B. Provider-aware budgeting и единый fallback

Компоненты: `app/services/ai/schemas.py`, `app/services/ai/hub.py`, `app/config.py`, `litellm_config.yaml`, `.env.example`.

1. Добавить local/cloud prompt variants и typed provider attempts.
2. Включить max tokens, prompt revision, schema, model/backend и circuit в cache identity.
3. Ограничить cloud → local единым deadline и нормализовать reason codes.
4. Возвращать actual backend/model из реального ответа, не выводить его из alias/строки.
5. После shadow-проверки убрать local fallback alias из LiteLLM и оставить cloud key rotation; Core API становится единственным владельцем cross-backend fallback.
6. Сохранить RED local-only и YELLOW sanitization; покрыть fail-closed DLP tests.

### 4C. Tone service и persistence

Компоненты: новый `response_variant_service.py`, `response_guard.py`/`truthfulness_guard.py`, `decision_journal.py`, router triage, DB models, migration `20260910_0014_*`.

1. Добавить append-only `decision_response_variants` и repository/service.
2. Реализовать `default/concise/detailed/regulatory` policies и strict schemas.
3. Добавить endpoint с current-decision, fingerprint, permission, idempotency и rate-limit checks.
4. Повторно применять structural/lexical/tone guard и возвращать только проверенный artifact.
5. Расширить apply необязательным `response_variant_id`, проверкой принадлежности и фиксацией operator edit.
6. Не изменять `DecisionEnvelope`, scenario/outcome/gates и `decision_version` при смене тона.

### 4D. UI и объяснимость

Компоненты: `UnifiedActionDock.tsx`, `useUnifiedDecision.ts`, `UnifiedDecisionPanel.tsx`, `lib/tasks.ts`, `lib/types.ts`.

1. Добавить отдельный AI Answer split-menu, loading/error/stale states и доступность с клавиатуры.
2. Переиспользовать защиту от затирания пользовательского черновика; изолировать variant draft по task/version/reply channel.
3. Передавать variant ID при apply и отслеживать локальную ручную правку.
4. Добавить компактный source badge и раскрываемые sanitised details.
5. Разнести routing score, fact completeness, readiness и response provenance; не объединять их в один confidence.

### 4E. Совместимость, replay и rollout controls

Компоненты: fixtures `ticket_quality`, API/contract tests, frontend tests, Docker/LiteLLM health checks.

1. Подготовить feature mode `off/shadow/enabled` для нового response orchestration и независимый UI flag.
2. Сравнить legacy и новый default response на исходных 99 снимках без отправки комментариев.
3. Добавить независимую размеченную tone/fallback-выборку, не использованную для prompt tuning.
4. Выполнить failure injection для всех зависимостей и проверить provenance/fallback.
5. Повысить `ANALYSIS_REVISION` только при включении нового default response/RAG semantics; on-demand variants сами по себе не делают старое решение stale.

---

## 8. Тестирование и критерии приёмки

### 8.1. Контракты и совместимость

- Envelope v1/v2 без provenance валидируется и отображается как source unknown.
- Новый `DecisionResponse` сериализует typed provenance без raw prompt и secret/provider error body.
- Tone request не меняет `decision_version`, facts, scenario, outcome, policy, plan или gates.
- Variant другого decision/task и stale version отклоняются.
- Историческое решение без достаточного v2 policy context требует reanalyze, а не синтезирует ответ из текущих случайных DB-данных.
- `response_variant_id` принадлежит текущему decision; edited comment повторно проходит command-boundary guard.

### 8.2. Capacity-Aware RAG

- При RED и direct Ollama в prompt не более `AI_OLLAMA_MAX_RAG_MATCHES`.
- При успешном YELLOW/GREEN cloud в prompt не более `AI_CLOUD_MAX_RAG_MATCHES`.
- При cloud failure локальная attempt содержит только local subset; cloud-only прецеденты отсутствуют.
- Несуществующий, отменённый, rejected/unresolved и ниже threshold прецедент не попадает в prompt.
- Недоступный reranker не превращает слабый RRF/cosine candidate в подтверждённый контекст.
- RAG candidate count, used count/profile и refs соответствуют фактически отправленному prompt.
- Изменение RAG corpus revision или лимитов не возвращает старый cache payload.

### 8.3. Provider routing, fallback и cache

- `intralink-chat` балансирует два cloud deployment; отказ одного ключа не раскрывается пользователю.
- После переключения владельца fallback LiteLLM не отправляет запрос в `intralink-local`; local attempt создаёт Core API.
- RED никогда не вызывает LiteLLM даже при недоступной Ollama.
- YELLOW cloud получает sanitised prompt, а deanonymization не восстанавливает неизвестную сущность.
- `max_tokens`, tone/prompt revision, schema, backend/model и circuit разделяют cache.
- Cache hit возвращает те же provenance/context refs; metadata не теряется.
- Redis outage не нарушает correctness и не запускает неограниченное число параллельных генераций.
- Общий deadline ограничивает cloud + local и возвращает deterministic fallback.

### 8.4. Tone и truthfulness

- `concise` содержит 1–2 предложения и сохраняет обязательный вопрос clarification.
- `detailed` содержит 3–5 допустимых пунктов либо явно деградирует к template; не добавляет registry/PowerShell/ID из модели.
- `regulatory` не вызывает AI Hub и совпадает с versioned template render.
- Ни один tone не меняет target status, action, expenses или approval.
- Planned/running/verified claims соответствуют phase/evidence этапа 3.
- Invalid JSON, prompt leakage, неизвестный PC/IP, неподтверждённое «выполнено» и неизвестный evidence ref приводят к template fallback.
- RED/credential/high-risk policy не допускает генеративную редактуру.
- Внутренняя сводка не появляется в public variant.

### 8.5. API, persistence и concurrency

- Повтор одинакового request возвращает тот же persisted variant при `regenerate=false`.
- Двойной клик не создаёт две внешние попытки благодаря single-flight.
- `regenerate=true` создаёт новую попытку в пределах rate limit, но не обходит guards.
- Variant записывается append-only с hash/provenance и читается после restart.
- Изменение заявки во время inference приводит к stale response, который не предлагается применить.
- Миграция `upgrade -> downgrade -> upgrade` проходит на пустой и заполненной копии PostgreSQL; исторические решения/commands не меняются.

### 8.6. UI

- Меню тонов управляется мышью и клавиатурой, имеет focus/aria states и не конфликтует с apply dropdown.
- Непустой пользовательский draft не затирается автоматически.
- Поздний response от другой заявки/версии не попадает в текущую textarea.
- Reply/internal drafts независимы; tone endpoint недоступен для internal mode.
- Badge показывает фактический `LiteLLM/Ollama/Регламент/Fallback`, а не requested alias.
- Degradation не окрашивает весь анализ как ошибочный, если template безопасен.
- Routing, completeness, readiness и response source визуально и семантически раздельны.

### 8.7. Replay и независимая оценка

- Все 99 исходных заявок присутствуют в новом comparison; исходный baseline не перезаписывается.
- Сохраняются 4/4 confirmed-регрессии этапа 2 и truthfulness-инварианты этапа 3.
- Для каждой записи сравниваются scenario/outcome/gates до и после: tone/refactoring не должен их менять без отдельно объяснённой причины.
- Сравниваются source/backend, fallback reason, RAG refs/count, response violations и текстовый diff.
- 95 `needs_review` не объявляются правильными автоматически.
- Независимая выборка содержит positive/negative cases для каждого tone, RED/YELLOW/GREEN, cloud success, local fallback, template fallback и stale race.
- Калибровка routing score выполняется отдельно; fallback rate и длина ответа не используются как суррогат точности.

### 8.8. Definition of Done для кода

1. Durable explicit analysis использует единый RAG/context path; GET остаётся read-only.
2. Фактическая Ollama всегда получает local-sized context, включая fallback после cloud.
3. Смена tone создаёт отдельный version-bound response artifact и не пересчитывает решение.
4. Каждый показанный текст имеет проверяемые source/backend/context/fallback metadata либо явный legacy unknown.
5. Все варианты проходят guard, а apply повторно валидирует фактический comment.
6. UI не затирает правку оператора и показывает четыре независимых аспекта качества.
7. Core API tests, frontend typecheck/lint/tests/build, replay, failure matrix и migration tests завершаются с exit code 0.
8. Документированы результаты benchmark/deadline и значения production Settings; наличие значений в `.env.example` не считается применением к целевой среде.

---

## 9. Безопасный rollout

1. **Инвентаризация:** зафиксировать текущие версии Core API/LiteLLM/Ollama/BGE-M3, активный alias, фактические `.env`-значения и состояние миграции `0013`. Не переносить секреты в отчёт.
2. **Локальная проверка:** unit/contract/integration tests, frontend build, migration на изолированной PostgreSQL, deterministic replay и provider mocks.
3. **Двойные prompt profiles при старом fallback:** добавить typed контракты и телеметрию, не меняя production route; доказать, что новый Core fallback работает в тестовой среде.
4. **Переключение владельца fallback:** убрать только local fallback mapping LiteLLM после проверки двух cloud deployments; включить Core API fallback в shadow. Rollback-флаг должен возвращать старый route без удаления provenance/table.
5. **Shadow default synthesis:** новый RAG/context и response artifact вычисляются и журналируются, но UI/применение продолжают использовать текущий канонический ответ. Сравниваются semantics, guard violations, latency и provider attempts.
6. **HITL canary:** открыть tone menu ограниченной группе операторов. Все варианты только предлагаются; отправка остаётся отдельным подтверждённым действием. Проверить overwrite protection, stale race и operator edits.
7. **Расширение:** включать новый default response только после отсутствия semantic drift scenario/outcome/gates, prompt/PII leakage, неправильного backend metadata и ложных completion claims на replay/shadow/canary.
8. **Production validation:** проверить миграцию на заполненной копии и целевой БД, health всех AI-зависимостей, фактические значения лимитов, p50/p95 latency, fallback distribution и bounded total deadline.
9. **Rollback:** выключить tone UI/endpoint и новый default orchestration feature mode; сохранить additive DB-таблицу, historical provenance и parser. Не удалять audit/variants и не откатывать решения. При возврате старого LiteLLM fallback явно пометить backend provenance как legacy/unknown.

Этап 4 считается реализованным и протестированным после Definition of Done. Он считается готовым к production rollout только после миграции целевой среды, проверки реальных alias/моделей/лимитов и успешного shadow/HITL canary. `active` в этом плане означает только включение presentation-контура AI-ответов; оно не разрешает автономное применение решения, отправку комментария или перевод сценарного автопилота в production `active`.
