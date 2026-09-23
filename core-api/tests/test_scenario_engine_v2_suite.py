"""Suite of comprehensive tests for Scenario Engine 2.0 (Clean Architecture)."""

try:
    import pytest
except ImportError:
    pytest = None
from app.services.scenarios import (
    CatalogPriorProvider,
    CoherenceGuard,
    ScenarioContext,
    ScenarioRouter,
    get_scenario_registry,
)
from shared.domain import FactBag


def test_registry_loading():
    """Проверяет корректность инициализации реестра сценариев из definitions/."""
    registry = get_scenario_registry()
    scenarios = registry.all()
    assert len(scenarios) >= 15
    keys = {s.definition.key for s in scenarios}
    assert "create_user" in keys
    assert "install_printer" in keys
    assert "grant_wlan" in keys
    assert "redirect" in keys
    assert "consultation" in keys


def test_catalog_prior_user_creation():
    """Проверяет Prior по ServiceId: 53 (Создание пользователя) даже при неполных данных."""
    registry = get_scenario_registry()
    router = ScenarioRouter(registry)

    task = {
        "Id": 141887,
        "ServiceId": 53,
        "ServiceName": "Создание нового пользователя сети",
        "Name": "Создание пользователя",
        "Description": "-",
    }
    ctx = ScenarioContext(task=task, facts=FactBag(revision=0, facts={}))
    res = router.route_result(ctx)

    assert res.scenario.definition.key == "create_user"
    assert res.score >= 0.95
    assert not res.is_ambiguous

    # Проверка исхода: неполнота данных не сбрасывает сценарий в fallback
    outcome = res.scenario.decide(ctx)
    assert outcome.kind == "clarification"
    assert getattr(outcome, "outcome_key", None) == "account_details_invalid"


def test_coherence_guard_cross_domain_collision():
    """Проверяет обнаружение коллизии Coherence Guard и редирект в статус 30."""
    registry = get_scenario_registry()
    router = ScenarioRouter(registry)

    task = {
        "Id": 141889,
        "ServiceId": 53,  # Заявка в учетках
        "ServiceName": "Создание нового пользователя сети",
        "Name": "Замена картриджа и ремонт принтера",
        "Description": "Принтер зажевал бумагу и полосит",
    }
    ctx = ScenarioContext(task=task, facts=FactBag(revision=0, facts={}))
    res = router.route_result(ctx)

    assert res.scenario.definition.key == "redirect"
    assert res.score >= 0.95
    assert any("cross_domain_collision" in r for r in res.reasons)


def test_unanchored_intent_routing():
    """Проверяет маршрутизацию unanchored заявки из общего раздела."""
    registry = get_scenario_registry()
    router = ScenarioRouter(registry)

    task = {
        "Id": 141890,
        "ServiceId": 16,  # 11. Общие вопросы
        "ServiceName": "Общие вопросы",
        "Name": "Переустановка операционной системы Windows",
        "Description": "Ноутбук сильно тормозит, требуется переустановка ОС",
    }
    ctx = ScenarioContext(task=task, facts=FactBag(revision=0, facts={}))
    res = router.route_result(ctx)

    assert res.scenario.definition.key == "os_reinstallation"
    assert res.score >= 0.90


def test_pinned_scenario_override():
    """Проверяет безусловный приоритет pinned сценария."""
    registry = get_scenario_registry()
    router = ScenarioRouter(registry)

    task = {
        "Id": 141891,
        "ServiceId": 53,
        "ServiceName": "Создание нового пользователя сети",
        "Name": "Создание учетной записи",
    }
    ctx = ScenarioContext(task=task, facts=FactBag(revision=0, facts={}))
    res = router.route_result(ctx, pinned_key="consultation", pinned_version=1)

    assert res.scenario.definition.key == "consultation"
    assert "pinned_version" in res.reasons


if __name__ == "__main__":
    test_registry_loading()
    test_catalog_prior_user_creation()
    test_coherence_guard_cross_domain_collision()
    test_unanchored_intent_routing()
    test_pinned_scenario_override()
    print("All Scenario Engine 2.0 tests passed successfully!")
