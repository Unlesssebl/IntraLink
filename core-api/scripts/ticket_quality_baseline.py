"""Export a read-only historical snapshot or evaluate it entirely offline.

Generated JSON/Markdown are artifacts; source data never touches disk.
Run from any directory with Python 3.13 (standard library only).
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import contextlib
from hashlib import sha256
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "core-api/tests/fixtures/ticket_quality"
REPORT = ROOT / "docs/reports/ticket-scenario-quality-baseline.md"
START, END = "2026-09-09 18:50:15+00", "2026-09-09 18:51:57+00"
WINDOW = f"created_at >= '{START}' AND created_at < '{END}'"

CORE_API = ROOT / "core-api"
if str(CORE_API) not in sys.path:
    sys.path.insert(0, str(CORE_API))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def query(sql):
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-X", "-U",
         "postgres", "-d", "intraservice", "-At", "-v", "ON_ERROR_STOP=1",
         "-c", "BEGIN READ ONLY; " + sql + "; COMMIT;"],
        cwd=ROOT, capture_output=True, encoding="utf-8", check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]


class Sanitizer:
    """Conservative projection plus consistent replacement of identifiers.

    Pseudonymized operational text is retained for regression, not public release.
    Unknown custom fields, directory dumps and attachment content are removed.
    """
    sensitive = re.compile(r"creator|executor|observer|coordinator|login|email|phone|identity|surname|patronymic|password|secret|token|credential|company|department|occupied_user|created_by|^editor$|^editorid$|participants", re.I)
    host = re.compile(r"\b([A-Za-zА-Яа-я]{2,8})([ -]*)(\d{3,5})\b")
    ip = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    path = re.compile(r"(?:\\\\[^\s,;\"<>]+|[A-Za-z]:\\[^\s,;\"<>]+)")
    email = re.compile(r"[\w.+-]+@[\w.-]+")
    url = re.compile(r"https?://[^\s\"<>]+")
    person = re.compile(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\b|\b[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*[А-ЯЁ][а-яё]+|\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.")

    def __init__(self, rows):
        self.private = set()
        self.ids = {}
        def collect(value, key=""):
            if isinstance(value, dict):
                for k, v in value.items():
                    collect(v, k)
            elif isinstance(value, list):
                for v in value:
                    collect(v, key)
            elif isinstance(value, str) and re.fullmatch(r"Creator|CreatorLogin|CreatorEmail|CreatorMobilePhone|CreatorPhone|Editor|surname|occupied_user", key, re.I):
                if len(value.strip()) >= 3 and value.strip().lower() not in {'нет', 'none', 'null'}:
                    self.private.add(value.strip())
        collect(rows)
        self.private = sorted(self.private, key=len, reverse=True)
        self.private_re = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(v) for v in self.private) + r')(?!\w)', re.I) if self.private else None

    def pseudonym(self, kind, value):
        canonical = value.casefold().replace(" ", "").replace("-", "")
        if kind == 'host':
            canonical = canonical.translate(str.maketrans({'т': 't', 'к': 'k', 'м': 'm'}))
        key = (kind, canonical)
        if key not in self.ids:
            self.ids[key] = 1000 + sum(k[0] == kind for k in self.ids)
        return self.ids[key]

    def text(self, value):
        if self.private_re:
            value = self.private_re.sub("<private>", value)
        value = self.email.sub("person@example.invalid", value)
        value = self.url.sub("https://example.invalid/resource", value)
        value = self.path.sub(lambda m: "\\\\files.example.invalid\\share\\path" + str(self.pseudonym("path", m[0])), value)
        def address(m):
            try:
                ipaddress.ip_address(m[0])
            except ValueError:
                return m[0]  # Keep software versions such as 8.3.27.2214.
            return "192.0.2." + str(self.pseudonym("ip", m[0]) - 999)
        value = self.ip.sub(address, value)
        # Preserve host prefix/separator and model names; only known host families.
        def host(m):
            if not re.match(r"^(?:ntemw|kmk|tkt|tnt|scsp|kzmp|ztep|ткт|кмк)", m[1], re.I):
                return m[0]
            return m[1] + m[2] + str(self.pseudonym("host", m[0]))
        value = self.host.sub(host, value)
        value = self.person.sub("<person_or_org>", value)
        value = re.sub(r'\bДодина\b', '<person>', value)
        value = re.sub(r"(?i)(пароль|password)\s*[:=]\s*\S+", r"\1: <secret>", value)
        return value

    def walk(self, value, key=""):
        if self.sensitive.search(key):
            return "<redacted>" if value is not None else None
        if key == 'pc_name':
            raw = value.get('value') if isinstance(value, dict) else value
            if isinstance(raw, str) and re.fullmatch(r'\d{3,5}', raw):
                matches = {n for (kind, host), n in self.ids.items()
                           if kind == 'host' and re.search(r'(?<!\d)' + re.escape(raw) + r'$', host)}
                if len(matches) == 1:
                    replacement = str(next(iter(matches)))
                    if isinstance(value, dict):
                        return {k: replacement if k == 'value' else self.walk(v, k) for k, v in value.items()}
                    return replacement
        if isinstance(value, dict):
            if key in {"_Users", "_Services", "_Statuses", "_Priorities"}:
                return {"omitted": True}
            return {k: self.walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.walk(v) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value


def annotation(row):
    tid = row["task_id"]
    result = dict(task_id=tid, status="needs_review", allowed_scenarios=[],
                  required_clarifications=[], forbidden_actions=[],
                  forbidden_outcomes=[], clarification_assessment="not_reviewed",
                  rationale="Нужна экспертная разметка по сохранённому контексту; исходное решение не является эталоном.")
    known = {
        140408: (["peripheral_setup"], [], "Колонки не являются принтером; полный ПК присутствует в описании."),
        140511: (["peripheral_diagnostics"], ["pc_name"], "Проводные наушники не являются принтером; необходимо имя ПК."),
        139762: (["pc_performance", "network_diagnostics"], [], "Медленны весь ПК и несколько приложений; 1С — сопутствующий симптом."),
        140686: (["os_reinstallation"], [], "Основной запрос — переустановка ОС; 1С упоминается как прошлое обращение."),
    }
    if tid in known:
        scenarios, fields, why = known[tid]
        result.update(status="confirmed", allowed_scenarios=scenarios,
                      required_clarifications=fields,
                      forbidden_actions=["install_printer"] if tid in (140408, 140511) else [],
                      forbidden_outcomes=["wrong_service"],
                      clarification_assessment="wrong_device" if tid in (140408, 140511) else "not_applicable",
                      rationale=why)
    return result


def export():
    rows = query(f"SELECT row_to_json(d) FROM (SELECT * FROM decision_records WHERE {WINDOW} ORDER BY task_id,version) d")
    sanitizer = Sanitizer(rows)
    sanitized = []
    for row in rows:
        context = row["context_json"]
        task = context.get("task", {})
        # Keep only regression-relevant task fields; all other original keys are inventoried.
        keep = {"Id", "Name", "Description", "ServiceId", "ServiceName", "StatusId", "StatusName", "Changed", "Created", "Field1102", "Field1103", "Field1104", "Field1111"}
        context["task"] = {k: v for k, v in task.items() if k in keep}
        context["omitted_task_keys"] = sorted(set(task) - keep)
        context.pop("ticket_fingerprint_task", None)
        context["attachments"] = {"omitted": True, "reason": "Attachment contents and filenames excluded"}
        sanitized.append(sanitizer.walk(row))
    FIXTURES.mkdir(parents=True, exist_ok=True)
    data = encoded(sanitized)
    (FIXTURES / "decisions.json").write_bytes(data)
    labels = encoded([annotation(row) for row in sanitized])
    (FIXTURES / "annotations.json").write_bytes(labels)
    counts = query("SELECT row_to_json(x) FROM (SELECT (SELECT count(*) FROM decision_steps) decision_steps, (SELECT count(*) FROM decision_feedback) decision_feedback, (SELECT count(*) FROM decision_applications) decision_applications, (SELECT count(*) FROM commands) commands) x")[0]
    manifest = dict(schema_version=1, start_inclusive=START, end_exclusive=END,
                    expected_count=99, actual_count=len(rows), unique_tasks=len({r['task_id'] for r in rows}),
                    discrepancy=len(rows) != 99 or len({r['task_id'] for r in rows}) != 99,
                    sha256={"decisions.json": sha256(data).hexdigest(), "annotations.json": sha256(labels).hexdigest()},
                    audit_table_counts_at_export=counts,
                    source="Docker PostgreSQL / intraservice / decision_records; READ ONLY",
                    privacy="Pseudonymized internal regression data, not approved for public distribution. Raw task projected; source values never written to disk.")
    (FIXTURES / "manifest.json").write_bytes(encoded(manifest))
    print(f"Exported {len(rows)} records; discrepancy={manifest['discrepancy']}")


def evaluate(rows, labels):
    by_id = {r["task_id"]: r for r in rows}
    if len(by_id) != len(rows) or {a['task_id'] for a in labels} != set(by_id) or len(labels) != len(rows):
        raise ValueError("Duplicate/missing records or annotations")
    confirmed = [a for a in labels if a["status"] == "confirmed"]
    findings = []
    for a in confirmed:
        e = by_id[a['task_id']]['envelope_json']
        outcome = e.get('outcome', {})
        errors = []
        if e.get('scenario_key') not in a['allowed_scenarios']:
            errors.append('scenario_mismatch')
        if outcome.get('outcome_key') in a['forbidden_outcomes']:
            errors.append('forbidden_redirect')
        if e.get('gates', {}).get('can_execute_action') and outcome.get('action') in a['forbidden_actions']:
            errors.append('forbidden_action_gate')
        if (a['clarification_assessment'] == 'wrong_device'
                and outcome.get('kind') == 'clarification'
                and outcome.get('outcome_key') == 'printer_ip_clarify'):
            errors.append('wrong_device_clarification')
        if outcome.get('kind') == 'clarification':
            missing = set(outcome.get('missing_fields', [])) | set(outcome.get('invalid_fields', []))
            if not set(a['required_clarifications']).issubset(missing):
                errors.append('required_clarification_missing')
        findings.append(dict(task_id=a['task_id'], actual=e.get('scenario_key'), expected=a['allowed_scenarios'], errors=errors))
    envelopes = [r['envelope_json'] for r in rows]
    return dict(total=len(rows), confirmed=len(confirmed), needs_review=len(labels)-len(confirmed),
                template=sum(e.get('response', {}).get('mode') == 'template' for e in envelopes),
                standard=sum(e.get('outcome', {}).get('outcome_key') == 'in_work_standard' for e in envelopes),
                confidence=dict(Counter(str(e.get('confidence')) for e in envelopes)),
                executable=sum(bool(e.get('gates', {}).get('can_execute_action')) for e in envelopes),
                by_scenario=dict(Counter(e.get('scenario_key') for e in envelopes)),
                findings=findings)


def check_fixtures(fixtures_dir: Path | None = None) -> dict[str, Any]:
    f_dir = fixtures_dir or FIXTURES
    manifest = json.loads((f_dir / 'manifest.json').read_text(encoding='utf-8'))
    for filename, digest in manifest['sha256'].items():
        data = (f_dir / filename).read_bytes().replace(b'\r\n', b'\n')
        if sha256(data).hexdigest() != digest:
            raise ValueError(f'Checksum mismatch: {filename}')
    return manifest


def report():
    manifest = check_fixtures()
    rows = json.loads((FIXTURES / 'decisions.json').read_text(encoding='utf-8'))
    labels = json.loads((FIXTURES / 'annotations.json').read_text(encoding='utf-8'))
    m = evaluate(rows, labels)
    correct = sum('scenario_mismatch' not in f['errors'] for f in m['findings'])
    lines = ['# Исходное качество сценариев заявок', '',
             f"Период: {START} ≤ created_at < {END}. Источник: PostgreSQL в Docker.", '',
             '## Покрытие и показатели', '',
             '| Показатель | Значение |', '|---|---:|',
             f"| Решения / уникальные заявки | {m['total']} / {len({r['task_id'] for r in rows})} |",
             f"| Подтверждённая разметка | {m['confirmed']} |",
             f"| Требуется экспертная проверка | {m['needs_review']} |",
             f"| Точность сценария на подтверждённой части | {correct}/{m['confirmed']} |",
             f"| Шаблонные ответы | {m['template']} |",
             f"| Стандартный ответ | {m['standard']} ({m['standard']/m['total']:.1%}) |",
             f"| Confidence | {m['confidence']} |",
             f"| Допуск инфраструктурного действия | {m['executable']} |",
             f"| Ошибочные редиректы в подтверждённой части | {sum('forbidden_redirect' in f['errors'] for f in m['findings'])} |",
             f"| Запрещённые допуски в подтверждённой части | {sum('forbidden_action_gate' in f['errors'] for f in m['findings'])} |",
             f"| Уточнения о неверном устройстве (подтверждённые) | {sum('wrong_device_clarification' in f['errors'] for f in m['findings'])} |",
             '', '## Подтверждённые регрессии', '', '| Заявка | Сценарий | Ожидается | Ошибки |', '|---|---|---|---|']
    for f in m['findings']:
        lines.append(f"| {f['task_id']} | {f['actual']} | {', '.join(f['expected'])} | {', '.join(f['errors'])} |")
    lines += ['', '## Распределение сценариев', '', '| Сценарий | Всего | Проверено | Правильный выбор среди проверенных |', '|---|---:|---:|---:|']
    for k, v in sorted(m['by_scenario'].items()):
        checked = [f for f in m['findings'] if f['actual'] == k]
        correct = sum('scenario_mismatch' not in f['errors'] for f in checked)
        accuracy = f'{correct}/{len(checked)}' if checked else 'не оценено'
        lines.append(f'| {k} | {v} | {len(checked)} | {accuracy} |')
    lines += ['', '## Ограничения', '',
              '- Подтверждены четыре заранее выявленных дефекта. Это целевая выборка ошибок, не оценка точности всех 99 заявок.',
              '- Ожидаемые имена сценариев являются семантическими метками разметки; наличие исполнителя не предполагается.',
              '- Два запроса уточнения относятся к неверному типу устройства. Остальные уточнения требуют экспертной оценки; техническая полнота текста автоматически не доказана.',
              '- Три допуска установки принтера находятся вне подтверждённой разметки. Ноль обнаруженных запрещённых допусков не доказывает их корректность.',
              '- Повторные вопросы и объём правок оператора: недоступно, нет завершённой разметки диалогов и обратной связи.',
              '- История сохранена в доступном контексте; живое состояние IntraService и содержимое вложений не запрашивались.',
              '- Данные псевдонимизированы для внутренней работы. Неизвестные поля, справочники и вложения исключены; публикация требует отдельной проверки свободного текста.',
              f"- Расхождение количества с исходными 99: {manifest['discrepancy']}.",
              '', '## Не завершённая экспертная разметка', '',
              ', '.join(str(a['task_id']) for a in labels if a['status'] == 'needs_review'), '',
              '## Воспроизведение', '',
              '`uv run python core-api/scripts/ticket_quality_baseline.py report`', '',
              'Команда проверяет контрольные суммы и строит отчёт из файлов без Docker, AI, IntraService или выполнения действий.', '']
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({k:v for k,v in m.items() if k != 'findings'}, ensure_ascii=False))


def adapt_row_to_task_and_comments(row: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ctx = row.get("context_json", {})
    task = dict(ctx.get("task", {}))
    raw = {}
    for fld in ("1102", "1103", "1104", "1111"):
        val = task.get(f"Field{fld}")
        if val is not None and str(val).strip():
            raw[fld] = str(val).strip()
    task["_field_meta"] = {"raw": raw}
    comments = [dict(c) for c in ctx.get("history_used", [])]
    return task, comments


class BlockedNetworkCallError(RuntimeError):
    pass


@contextlib.contextmanager
def prevent_network():
    orig_create_connection = socket.create_connection
    orig_connect = socket.socket.connect

    def guarded_create_connection(*args, **kwargs):
        raise BlockedNetworkCallError("Unexpected network call during offline replay")

    def guarded_connect(self, address):
        raise BlockedNetworkCallError(f"Unexpected network call to {address} during offline replay")

    socket.create_connection = guarded_create_connection
    socket.socket.connect = guarded_connect
    try:
        yield
    finally:
        socket.create_connection = orig_create_connection
        socket.socket.connect = orig_connect


async def _init_replay_db(policies_path: Path):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.database.db import Base, ResponseTemplate, ResolutionPolicy

    policies_data = json.loads(policies_path.read_text(encoding="utf-8"))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[ResponseTemplate.__table__, ResolutionPolicy.__table__],
        )
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        for t in policies_data.get("response_templates", []):
            session.add(
                ResponseTemplate(
                    **{k: v for k, v in t.items() if k not in ("created_at", "updated_at")}
                )
            )
        for p in policies_data.get("resolution_policies", []):
            session.add(
                ResolutionPolicy(
                    **{k: v for k, v in p.items() if k not in ("created_at", "updated_at")}
                )
            )
        await session.commit()
    return engine, session_factory


async def replay_async(
    output_dir: Path,
    decisions_path: Path | None = None,
    policies_path: Path | None = None,
) -> list[dict[str, Any]]:
    check_fixtures()
    core_api_path = ROOT / "core-api"
    if str(core_api_path) not in sys.path:
        sys.path.insert(0, str(core_api_path))

    dec_path = decisions_path or (FIXTURES / "decisions.json")
    pol_path = policies_path or (FIXTURES / "policies.json")
    rows = json.loads(dec_path.read_text(encoding="utf-8"))

    engine, session_factory = await _init_replay_db(pol_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    from app.services.scenario_decision import ScenarioDecisionService
    from app.services.scenario_pipeline import FactCollectionPlanner

    results = []
    with prevent_network():
        async with session_factory() as session:
            planner = FactCollectionPlanner(include_llm=False)
            service = ScenarioDecisionService(
                session,
                fact_planner=planner,
                ai_enabled=False,
            )
            for row in rows:
                task, comments = adapt_row_to_task_and_comments(row)
                task_id = row["task_id"]
                envelope = await service.analyze(
                    task=task,
                    comments=comments,
                    diagnostics=None,
                    kb_matches=[],
                )
                res_rec = {
                    "task_id": task_id,
                    "version": row.get("version", 1),
                    "analysis_kind": row.get("analysis_kind", "triage"),
                    "status": "finalized",
                    "created_at": row.get("created_at"),
                    "finalized_at": row.get("finalized_at"),
                    "envelope_json": envelope.model_dump(mode="json"),
                    "context_json": row.get("context_json"),
                }
                results.append(res_rec)

    await engine.dispose()

    data = encoded(results)
    out_file = output_dir / "decisions-after.json"
    out_file.write_bytes(data)

    source_bytes = dec_path.read_bytes().replace(b"\r\n", b"\n")
    manifest = {
        "schema_version": 1,
        "count": len(results),
        "source_decisions_sha256": sha256(source_bytes).hexdigest(),
        "sha256": {
            "decisions-after.json": sha256(data).hexdigest(),
        },
    }
    (output_dir / "manifest.json").write_bytes(encoded(manifest))
    print(f"Replay complete: {len(results)} records written to {out_file}")
    return results


def replay(
    output_dir: Path | str | None = None,
    decisions_path: Path | str | None = None,
    policies_path: Path | str | None = None,
    run_compare: bool = False,
) -> list[dict[str, Any]]:
    out_dir = Path(output_dir) if output_dir else (FIXTURES / "replay")
    d_path = Path(decisions_path) if decisions_path else None
    p_path = Path(policies_path) if policies_path else None
    results = asyncio.run(replay_async(out_dir, d_path, p_path))
    if run_compare:
        compare(
            candidate_path=out_dir / "decisions-after.json",
            baseline_path=d_path,
            output_path=out_dir / "comparison.md",
        )
    return results


def diff_envelope(base_e: dict[str, Any], cand_e: dict[str, Any]) -> dict[str, Any]:
    diffs: dict[str, Any] = {}
    if base_e.get("scenario_key") != cand_e.get("scenario_key"):
        diffs["scenario_key"] = {
            "baseline": base_e.get("scenario_key"),
            "candidate": cand_e.get("scenario_key"),
        }

    base_out = base_e.get("outcome") or {}
    cand_out = cand_e.get("outcome") or {}
    out_diff = {}
    for k in ("kind", "outcome_key", "action", "parameters", "missing_fields", "invalid_fields"):
        bv = base_out.get(k)
        cv = cand_out.get(k)
        if bv != cv:
            out_diff[k] = {"baseline": bv, "candidate": cv}
    if out_diff:
        diffs["outcome"] = out_diff

    base_pol = base_e.get("policy") or {}
    cand_pol = cand_e.get("policy") or {}
    pol_diff = {}
    for k in ("status_id", "status_name", "requires_approval"):
        bv = base_pol.get(k)
        cv = cand_pol.get(k)
        if bv != cv:
            pol_diff[k] = {"baseline": bv, "candidate": cv}
    if pol_diff:
        diffs["policy"] = pol_diff

    base_gates = base_e.get("gates") or {}
    cand_gates = cand_e.get("gates") or {}
    gates_diff = {}
    for k in ("can_execute_action", "can_send_response", "requires_approval", "blocked_reasons"):
        bv = base_gates.get(k)
        cv = cand_gates.get(k)
        if bv != cv:
            gates_diff[k] = {"baseline": bv, "candidate": cv}
    if gates_diff:
        diffs["gates"] = gates_diff

    base_facts = base_e.get("facts_summary") or {}
    cand_facts = cand_e.get("facts_summary") or {}
    facts_diff = {}
    for fk in ("pc_name", "printer_name", "printer_address", "device_type", "printer_targets"):
        bv = base_facts.get(fk)
        cv = cand_facts.get(fk)
        if fk == "device_type" and bv is None:
            continue
        if bv != cv:
            facts_diff[fk] = {"baseline": bv, "candidate": cv}
    if facts_diff:
        diffs["facts"] = facts_diff

    return diffs


def compare(
    candidate_path: Path | str,
    baseline_path: Path | str | None = None,
    annotations_path: Path | str | None = None,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    c_path = Path(candidate_path)
    if c_path.is_dir():
        c_path = c_path / "decisions-after.json"
    b_path = Path(baseline_path) if baseline_path else (FIXTURES / "decisions.json")
    a_path = Path(annotations_path) if annotations_path else (FIXTURES / "annotations.json")

    base_rows = json.loads(b_path.read_text(encoding="utf-8"))
    cand_rows = json.loads(c_path.read_text(encoding="utf-8"))
    labels = json.loads(a_path.read_text(encoding="utf-8"))

    base_ids = {r["task_id"] for r in base_rows}
    cand_ids = {r["task_id"] for r in cand_rows}
    common_ids = base_ids & cand_ids
    active_labels = [a for a in labels if a["task_id"] in common_ids]
    base_eval_rows = [r for r in base_rows if r["task_id"] in common_ids]
    cand_eval_rows = [r for r in cand_rows if r["task_id"] in common_ids]

    base_m = evaluate(base_eval_rows, active_labels)
    cand_m = evaluate(cand_eval_rows, active_labels)

    by_cand = {r["task_id"]: r for r in cand_rows}
    by_base = {r["task_id"]: r for r in base_rows}

    diffs_by_task = {}
    for tid, b_row in by_base.items():
        if tid in by_cand:
            d = diff_envelope(b_row["envelope_json"], by_cand[tid]["envelope_json"])
            if d:
                diffs_by_task[tid] = d

    lines = [
        "# Сравнение решений сценариев заявок (Baseline vs Candidate)",
        "",
        f"- **Baseline:** `{b_path.name}` ({len(base_rows)} решений)",
        f"- **Candidate:** `{c_path.name}` ({len(cand_rows)} решений)",
        "",
        "## Сводные метрики",
        "",
        "| Метрика | Исходный Baseline | Candidate Replay | Дельта |",
        "|---|---:|---:|---:|",
        f"| Всего решений | {base_m['total']} | {cand_m['total']} | 0 |",
        f"| Точность на подтверждённой выборке | {sum('scenario_mismatch' not in f['errors'] for f in base_m['findings'])}/{base_m['confirmed']} | {sum('scenario_mismatch' not in f['errors'] for f in cand_m['findings'])}/{cand_m['confirmed']} | {sum('scenario_mismatch' not in f['errors'] for f in cand_m['findings']) - sum('scenario_mismatch' not in f['errors'] for f in base_m['findings']):+d} |",
        f"| Запрещённые редиректы | {sum('forbidden_redirect' in f['errors'] for f in base_m['findings'])} | {sum('forbidden_redirect' in f['errors'] for f in cand_m['findings'])} | {sum('forbidden_redirect' in f['errors'] for f in cand_m['findings']) - sum('forbidden_redirect' in f['errors'] for f in base_m['findings']):+d} |",
        f"| Запрещённые допуски | {sum('forbidden_action_gate' in f['errors'] for f in base_m['findings'])} | {sum('forbidden_action_gate' in f['errors'] for f in cand_m['findings'])} | {sum('forbidden_action_gate' in f['errors'] for f in cand_m['findings']) - sum('forbidden_action_gate' in f['errors'] for f in base_m['findings']):+d} |",
        f"| Ошибочные уточнения устройства | {sum('wrong_device_clarification' in f['errors'] for f in base_m['findings'])} | {sum('wrong_device_clarification' in f['errors'] for f in cand_m['findings'])} | {sum('wrong_device_clarification' in f['errors'] for f in cand_m['findings']) - sum('wrong_device_clarification' in f['errors'] for f in base_m['findings']):+d} |",
        f"| Идентичные решения (0 diff) | - | {len(base_rows) - len(diffs_by_task)}/{len(base_rows)} | - |",
        f"| Решения с различиями | - | {len(diffs_by_task)}/{len(base_rows)} | - |",
        "",
        "## Подтверждённые регрессии (4 контрольных случая)",
        "",
        "| Заявка | Ожидалось | Baseline ошибки | Candidate ошибки | Статус |",
        "|---|---|---|---|---|",
    ]
    base_find_by_id = {f["task_id"]: f for f in base_m["findings"]}
    for f in cand_m["findings"]:
        tid = f["task_id"]
        bf = base_find_by_id.get(tid, {})
        b_errs = bf.get("errors", [])
        c_errs = f["errors"]
        status = "✅ Исправлено" if b_errs and not c_errs else ("⚠️ Без изменений" if b_errs and c_errs else "ℹ️")
        lines.append(f"| {tid} | {', '.join(f['expected'])} | {', '.join(b_errs) or '—'} | {', '.join(c_errs) or '—'} | {status} |")

    lines.extend([
        "",
        "## Различия в неразмеченной выборке (needs_review)",
        "",
        "> [!NOTE]",
        "> Данные различия отражают семантические изменения логики или ограничения реконструкции входа (например, отсутствие сырого XML Data). Они не оцениваются автоматически как ошибка или успех до экспертной разметки.",
        "",
    ])
    unannotated_diffs = {tid: d for tid, d in diffs_by_task.items() if tid not in base_find_by_id}
    if unannotated_diffs:
        lines.extend([
            "| Заявка | Область различий | Baseline | Candidate |",
            "|---|---|---|---|",
        ])
        for tid, d in sorted(unannotated_diffs.items()):
            for scope, vals in d.items():
                if isinstance(vals, dict) and "baseline" in vals and "candidate" in vals:
                    lines.append(f"| {tid} | `{scope}` | `{vals['baseline']}` | `{vals['candidate']}` |")
                elif isinstance(vals, dict):
                    for subk, subvals in vals.items():
                        lines.append(f"| {tid} | `{scope}.{subk}` | `{subvals.get('baseline')}` | `{subvals.get('candidate')}` |")
    else:
        lines.append("Различий в неразмеченной выборке не обнаружено.")

    out_text = "\n".join(lines) + "\n"
    out_p = Path(output_path) if output_path else c_path.parent / "comparison.md"
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(out_text, encoding="utf-8")
    print(f"Comparison complete: report written to {out_p}")
    print(f"Total: {len(base_rows)}, Identical: {len(base_rows) - len(diffs_by_task)}, Changed: {len(diffs_by_task)}")
    return {
        "total": len(base_rows),
        "identical": len(base_rows) - len(diffs_by_task),
        "changed": len(diffs_by_task),
        "diffs": diffs_by_task,
        "report_path": str(out_p),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("export", help="Export historical decisions from Docker PostgreSQL")
    sub.add_parser("report", help="Validate fixtures checksums and generate baseline report")

    replay_parser = sub.add_parser("replay", help="Replay 99 decisions offline through ScenarioDecisionService")
    replay_parser.add_argument("--output-dir", default=str(FIXTURES / "replay"), help="Directory for decisions-after.json and manifest.json")
    replay_parser.add_argument("--decisions", default=None, help="Path to input decisions.json (default: fixtures/decisions.json)")
    replay_parser.add_argument("--policies", default=None, help="Path to policies.json (default: fixtures/policies.json)")
    replay_parser.add_argument("--compare", action="store_true", help="Automatically run compare against baseline after replay")

    compare_parser = sub.add_parser("compare", help="Compare candidate decisions with baseline")
    compare_parser.add_argument("--candidate", required=True, help="Path to candidate decisions-after.json or replay directory")
    compare_parser.add_argument("--baseline", default=None, help="Path to baseline decisions.json (default: fixtures/decisions.json)")
    compare_parser.add_argument("--annotations", default=None, help="Path to annotations.json (default: fixtures/annotations.json)")
    compare_parser.add_argument("--output", default=None, help="Path to write comparison.md (default: <candidate_dir>/comparison.md)")

    args = parser.parse_args()
    if args.command == "export":
        export()
    elif args.command == "report":
        report()
    elif args.command == "replay":
        replay(
            output_dir=args.output_dir,
            decisions_path=args.decisions,
            policies_path=args.policies,
            run_compare=args.compare,
        )
    elif args.command == "compare":
        compare(
            candidate_path=args.candidate,
            baseline_path=args.baseline,
            annotations_path=args.annotations,
            output_path=args.output,
        )
