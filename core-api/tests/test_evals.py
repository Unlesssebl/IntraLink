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
