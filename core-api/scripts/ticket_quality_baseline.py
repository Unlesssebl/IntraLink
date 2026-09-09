"""Export a read-only historical snapshot or evaluate it entirely offline.

Generated JSON/Markdown are artifacts; source data never touches disk.
Run from any directory with Python 3.13 (standard library only).
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import ipaddress
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "core-api/tests/fixtures/ticket_quality"
REPORT = ROOT / "docs/reports/ticket-scenario-quality-baseline.md"
START, END = "2026-09-09 18:50:15+00", "2026-09-09 18:51:57+00"
WINDOW = f"created_at >= '{START}' AND created_at < '{END}'"


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


def report():
    manifest = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf-8'))
    for filename, digest in manifest['sha256'].items():
        if sha256((FIXTURES / filename).read_bytes()).hexdigest() != digest:
            raise ValueError(f'Checksum mismatch: {filename}')
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['export', 'report'])
    args = parser.parse_args()
    export() if args.command == 'export' else report()
