"""Tests for semantic-enhanced CoherenceGuard."""


from core.intraservice.dto import TaskDTO
from worker.src.scenarios.coherence_guard import CoherenceGuard, CoherenceStatus


def test_coherence_guard_semantic_divergence():
    """Verify that high-confidence semantic divergence penalizes mismatched candidate."""
    guard = CoherenceGuard()

    # Ticket text has no exact keywords for printer or ad, but semantic scores show strong AD intent
    task = TaskDTO(
        Id=8801,
        Name="Срочный вопрос по учетной записи",
        Description="Меня выкинуло из аккаунта, помогите войти",
    )

    semantic_scores = {
        "ad_password_reset": 0.92,
        "install_printer": 0.15,
    }

    # Checking candidate 'install_printer' - should be flagged as DIVERGENT due to AD semantic score
    res = guard.evaluate(
        candidate_key="install_printer",
        task=task,
        semantic_scores=semantic_scores,
    )
    assert res.status == CoherenceStatus.DIVERGENT
    assert res.confidence_delta == -0.40
    assert res.divergent_scenario == "ad_password_reset"
    assert any("ad_password_reset" in r for r in res.reasons)


def test_coherence_guard_cold_start_graceful_lexical():
    """Verify that when semantic_scores is None or empty, lexical guard functions without error."""
    guard = CoherenceGuard()

    task = TaskDTO(
        Id=8802,
        Name="Не печатает МФУ",
        Description="Зажевало бумагу в принтере",
    )

    res = guard.evaluate(
        candidate_key="install_printer",
        task=task,
        semantic_scores=None,
    )
    assert res.status == CoherenceStatus.COHERENT
    assert res.confidence_delta == 0.10
