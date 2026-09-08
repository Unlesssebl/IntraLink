from app.services.evals import apply_release_gate, evaluate, temporal_split, validate_records


def _row(index: int, *, dlp_safe: bool = True, status_ok: bool = True) -> dict:
    return {
        "id": f"anonymous-{index}",
        "closed_at": f"2026-01-{(index % 28) + 1:02d}T10:00:00+00:00",
        "query": "sanitized printer incident",
        "expected_ids": [f"kb-{index}"],
        "retrieved_ids": [f"kb-{index}", "kb-other"],
        "expected_status_id": 28,
        "predicted_status_id": 28 if status_ok else 30,
        "expected_action": "diagnose_host",
        "predicted_action": "diagnose_host",
        "effective_mode": "auto",
        "dlp_safe": dlp_safe,
    }


def test_eval_reports_metrics_and_temporal_split():
    rows = validate_records([_row(i) for i in range(10)], min_cases=10)
    split = temporal_split(rows)
    assert [len(split[name]) for name in ("corpus", "validation", "test")] == [7, 1, 2]
    report = evaluate(split["test"])
    assert report["metrics"]["recall_at_5"] == 1.0
    assert report["metrics"]["mrr_at_5"] == 1.0
    assert report["safety"] == {"dlp_failures": 0, "unsafe_autonomy": 0}


def test_release_gate_blocks_safety_and_regression():
    report = evaluate([_row(1, dlp_safe=False, status_ok=False)])
    gate = apply_release_gate(
        report,
        {"metrics": {"recall_at_5": 1.0, "mrr_at_5": 1.0, "triage_accuracy": 1.0, "safe_recommendation_precision": 1.0}},
    )
    assert gate["passed"] is False
    assert any("DLP probes failed" in reason for reason in gate["reasons"])
    assert any("triage_accuracy regressed" in reason for reason in gate["reasons"])


def test_eval_handles_no_match_and_partial_recall():
    rows = [
        # Позитивный кейс: 2 ожидалось, найден 1 -> Recall = 0.5, Hit = 1.0
        {
            "id": "case-pos-1",
            "closed_at": "2026-01-01T10:00:00+00:00",
            "query": "printer spooler issue",
            "expected_ids": ["kb-101", "kb-102"],
            "retrieved_ids": ["kb-101", "kb-999"],
            "dlp_safe": True,
        },
        # No-match кейс 1: ожидается [], получено [] -> no-match успех
        {
            "id": "case-neg-1",
            "closed_at": "2026-01-02T10:00:00+00:00",
            "query": "buy office paper",
            "expected_ids": [],
            "retrieved_ids": [],
            "dlp_safe": True,
        },
        # No-match кейс 2: ожидается [], получено ['kb-fake'] -> no-match ошибка
        {
            "id": "case-neg-2",
            "closed_at": "2026-01-03T10:00:00+00:00",
            "query": "clean the kitchen",
            "expected_ids": [],
            "retrieved_ids": ["kb-fake"],
            "dlp_safe": True,
        },
    ]
    valid = validate_records(rows, min_cases=3)
    assert len(valid) == 3

    report = evaluate(valid)
    assert report["cases"] == 3
    assert report["positive_cases"] == 1
    assert report["no_match_cases"] == 2
    # Для позитивного: 1 из 2 ожидаемых найден
    assert report["metrics"]["recall_at_5"] == 0.5
    assert report["metrics"]["hit_at_5"] == 1.0
    assert report["metrics"]["mrr_at_5"] == 1.0
    # Для негативных: 1 верный из 2 -> 0.5
    assert report["metrics"]["no_match_accuracy"] == 0.5


def test_rag_eval_dataset_fixture():
    import json
    from pathlib import Path

    fixture_path = Path(__file__).parent / "fixtures" / "rag_eval_dataset.jsonl"
    assert fixture_path.exists()

    with fixture_path.open("r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

    valid = validate_records(records, min_cases=30)
    assert len(valid) >= 30

    report = evaluate(valid)
    assert report["cases"] == 30
    assert report["positive_cases"] == 20
    assert report["no_match_cases"] == 10
    assert report["metrics"]["recall_at_5"] >= 0.9
    assert report["metrics"]["no_match_accuracy"] == 1.0

