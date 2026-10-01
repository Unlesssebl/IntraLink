from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.ad.ou_resolver import (
    AdOuResolver,
)
from core.ad.ou_routing import (
    AdOuRoutingConfig,
    CategoryRoutingMode,
    CategoryRule,
    DepartmentRule,
    OrganizationRule,
    ServiceRoutingRule,
)
from core.automation.frame_extractor import CaseFrameExtractor
from core.automation.snapshot import (
    TicketSnapshotFactory,
    build_ou_routing_context,
)
from core.intraservice.dto import (
    TaskCategoryDTO,
    TaskDTO,
    normalize_category_pairs,
)
from core.intraservice.parser import parse_custom_fields
from worker.src.tasks.command_dispatcher import _validate_mutation_preflight

# ---------------------------------------------------------------------------
# Test Fixtures & Minimal Routing Config
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_routing_config():
    return AdOuRoutingConfig(
        version=1,
        services={
            53: ServiceRoutingRule(
                categories={
                    57: CategoryRule(
                        expected_names=['АО "НТЗ "ТЭМ-ПО"', "АО НТЗ ТЭМПО"],
                        mode=CategoryRoutingMode.organization,
                        organization_key="ntz_tempo",
                    ),
                    44: CategoryRule(
                        expected_names=["Филиал в г.Москва", "Филиал в г. Москва"],
                        mode=CategoryRoutingMode.branch_hint,
                        branch_key="moscow",
                    ),
                    70: CategoryRule(
                        expected_names=["ПТФК ЗТЭО"],
                        mode=CategoryRoutingMode.review_required,
                        organization_key="ptfk_zteo",
                    ),
                }
            )
        },
        organizations={
            "ntz_tempo": OrganizationRule(
                aliases=["АО НТЗ ТЭМПО", 'АО "НТЗ "ТЭМ-ПО"'],
                scope_dn="OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
                departments={
                    "atx": DepartmentRule(
                        aliases=["АТХ", "ПШМ АТХ"],
                        target_relative_dn="OU=ПШМ АТХ",
                    ),
                    "buh": DepartmentRule(
                        aliases=["Бухгалтерия", "Отдел бухгалтерии"],
                        target_relative_dn="OU=Бухгалтерия",
                    ),
                },
            ),
            "tk_tempo": OrganizationRule(
                aliases=["ООО ТК ТЭМПО", "ТК ТЭМПО"],
                scope_dn="OU=ООО ТК ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
                departments={},
            ),
            "ptfk_zteo": OrganizationRule(
                aliases=["АО ПТФК ЗТЭО", "ПТФК ЗТЭО"],
                scope_dn="OU=АО ПТФК ЗТЭО,OU=Предприятия,DC=corp,DC=tempo",
                departments={},
            ),
        },
    )


@pytest.fixture
def mock_cached_ous():
    return [
        "OU=ПШМ АТХ,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "OU=Бухгалтерия,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "OU=Отдел продаж,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "OU=Отдел маркетинга,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "OU=Склад готовой продукции,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "OU=Транспортный цех,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "OU=Филиал в г.Москва,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "OU=Бухгалтерия,OU=Филиал в г.Москва,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "OU=Склад,OU=Филиал в г.Москва,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        # External / System OUs
        "OU=Disabled Users,DC=corp,DC=tempo",
        "OU=Service Accounts,DC=corp,DC=tempo",
        "OU=Бухгалтерия,OU=ООО ТК ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
    ]


# ---------------------------------------------------------------------------
# 1. Разбор одной и нескольких категорий (TaskCategoryDTO, разделитель ||)
# ---------------------------------------------------------------------------

def test_category_parsing_single_and_multiple_and_pipe_separator():
    # Single category
    single = normalize_category_pairs(categories="57: АО \"НТЗ \"ТЭМ-ПО\"")
    assert len(single) == 1
    assert single[0].id == 57
    assert single[0].name == 'АО "НТЗ "ТЭМ-ПО"'

    # Pipe-separated categories
    multi = normalize_category_pairs(categories='57: АО "НТЗ "ТЭМ-ПО" || 44: Филиал в г.Москва')
    assert len(multi) == 2
    assert multi[0].id == 57
    assert multi[0].name == 'АО "НТЗ "ТЭМ-ПО"'
    assert multi[1].id == 44
    assert multi[1].name == "Филиал в г.Москва"

    # Separate categories and category_ids
    from_lists = normalize_category_pairs(
        categories=["АО НТЗ ТЭМПО", "Филиал в г.Москва"],
        category_ids=[57, 44],
    )
    assert len(from_lists) == 2
    assert from_lists[0].id == 57
    assert from_lists[0].name == "АО НТЗ ТЭМПО"
    assert from_lists[1].id == 44
    assert from_lists[1].name == "Филиал в г.Москва"

    # TaskDTO integration
    task = TaskDTO(
        id=142536,
        name="Создание учетной записи",
        categories='57: АО "НТЗ "ТЭМ-ПО" || 44: Филиал в г.Москва',
    )
    assert len(task.normalized_categories) == 2
    assert task.normalized_categories[0].id == 57
    assert task.normalized_categories[1].id == 44


# ---------------------------------------------------------------------------
# 2. Несовпадение category ID / name (expected_names)
# ---------------------------------------------------------------------------

def test_category_id_name_mismatch_detection(sample_routing_config):
    task = TaskDTO(
        id=142536,
        service_id=53,
        name="Создание учетной записи",
        categories="57: ООО Рога и Копыта",
    )
    routing_ctx = build_ou_routing_context(
        task=task,
        routing_config=sample_routing_config,
        policy_hash="test_hash",
        policy_version=1,
    )
    assert len(routing_ctx.categories) == 1
    cat_item = routing_ctx.categories[0]
    assert cat_item.id == 57
    assert cat_item.name == "ООО Рога и Копыта"
    assert cat_item.name_mismatch is True
    assert "category_name_mismatch:57" in routing_ctx.diagnostics

    # Resolver must ignore mismatched category as trusted organization
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )
    res = resolver.resolve(
        company=None,
        department="ПШМ АТХ",
        routing_context=routing_ctx,
        cached_ous=["OU=ПШМ АТХ,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"],
    )
    # Since company is None and category has name mismatch, it cannot auto-resolve ntz_tempo scope
    assert res.target_ou is None or res.resolved_scope_dn is None


# ---------------------------------------------------------------------------
# 3. Exact category и organization mapping (запрет fuzzy для категорий/организаций)
# ---------------------------------------------------------------------------

def test_exact_category_and_organization_mapping(sample_routing_config, mock_cached_ous):
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )

    # 1. Exact Category mapping
    task_cat = TaskDTO(
        id=1,
        service_id=53,
        name="Тест",
        categories='57: АО "НТЗ "ТЭМ-ПО"',
    )
    ctx_cat = build_ou_routing_context(task_cat, routing_config=sample_routing_config)
    res_cat = resolver.resolve(
        company=None,
        department="АТХ",
        routing_context=ctx_cat,
        cached_ous=mock_cached_ous,
    )
    assert res_cat.target_ou == "OU=ПШМ АТХ,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    assert res_cat.resolved_scope_dn == "OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    assert "category_mapping" in res_cat.evidence
    assert "department_alias" in res_cat.evidence

    # 2. Exact Company mapping
    res_comp = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="АТХ",
        routing_context=None,
        cached_ous=mock_cached_ous,
    )
    assert res_comp.target_ou == "OU=ПШМ АТХ,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    assert "employee_company_mapping" in res_comp.evidence

    # 3. Strict prohibition of fuzzy company matching (fuzzy on company must fail)
    res_fuzzy_comp = resolver.resolve(
        company="ТЭМПО ЗАВОД ТРУБ",  # Not an exact alias
        department="АТХ",
        routing_context=None,
        cached_ous=mock_cached_ous,
    )
    # Scope must not be resolved via fuzzy company matching
    assert res_fuzzy_comp.resolved_scope_dn is None


# ---------------------------------------------------------------------------
# 4. Конфликт организации сотрудника и категории
# ---------------------------------------------------------------------------

def test_target_company_and_category_scope_conflict(sample_routing_config, mock_cached_ous):
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )

    # Company indicates tk_tempo, but category indicates ntz_tempo
    task = TaskDTO(
        id=2,
        service_id=53,
        name="Тест",
        categories='57: АО "НТЗ "ТЭМ-ПО"',
    )
    ctx = build_ou_routing_context(task, routing_config=sample_routing_config)

    res = resolver.resolve(
        company="ООО ТК ТЭМПО",
        department="Бухгалтерия",
        routing_context=ctx,
        cached_ous=mock_cached_ous,
    )

    assert res.target_ou is None
    assert res.conflict_reason == "target_company_category_conflict"
    assert len(res.candidates) >= 2
    candidate_dns = [c.distinguished_name for c in res.candidates]
    assert "OU=Бухгалтерия,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo" in candidate_dns
    assert "OU=Бухгалтерия,OU=ООО ТК ТЭМПО,OU=Предприятия,DC=corp,DC=tempo" in candidate_dns
    # Ensure no organization root scope DN is offered as a target candidate (P1 finding fix)
    assert "OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo" not in candidate_dns
    assert "OU=ООО ТК ТЭМПО,OU=Предприятия,DC=corp,DC=tempo" not in candidate_dns


# ---------------------------------------------------------------------------
# 5. Fallback по организации заявителя (только при наличии целевого отдела)
# ---------------------------------------------------------------------------

def test_applicant_company_fallback(sample_routing_config, mock_cached_ous):
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )

    task = TaskDTO(
        id=3,
        service_id=53,
        name="Тест",
        creator_company_name='АО "НТЗ "ТЭМ-ПО"',
    )
    ctx = build_ou_routing_context(task, routing_config=sample_routing_config)

    # 1. Fallback succeeds when target department is specified and resolvable
    res_ok = resolver.resolve(
        company=None,
        department="АТХ",
        routing_context=ctx,
        cached_ous=mock_cached_ous,
    )
    assert res_ok.target_ou == "OU=ПШМ АТХ,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    assert "applicant_company_fallback" in res_ok.evidence

    # 2. Fallback fails if target department is missing (prevent blind fallback)
    res_no_dept = resolver.resolve(
        company=None,
        department=None,
        routing_context=ctx,
        cached_ous=mock_cached_ous,
    )
    assert res_no_dept.target_ou is None


# ---------------------------------------------------------------------------
# 6. Запрет подстановки подразделения заявителя как целевого
# ---------------------------------------------------------------------------

def test_applicant_department_never_substitutes_target_department(sample_routing_config, mock_cached_ous):
    # Test parser: applicant company/categories are never substituted into employee company
    entities, _ = parse_custom_fields(
        None,
        raw_task={
            "ApplicantCompany": 'АО "НТЗ "ТЭМ-ПО"',
            "Categories": '57: АО "НТЗ "ТЭМ-ПО"',
        },
    )
    assert not entities.company

    # Test resolver: mismatched applicant department does NOT penalize target department score
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )
    task = TaskDTO(
        id=4,
        service_id=53,
        name="Тест",
        creator_company_name='АО "НТЗ "ТЭМ-ПО"',
        creator_department="Бухгалтерия",  # Applicant department
    )
    ctx = build_ou_routing_context(task, routing_config=sample_routing_config)

    # Target department is "Транспортный цех"
    res = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="Транспортный цех",
        routing_context=ctx,
        cached_ous=mock_cached_ous,
    )
    assert res.target_ou == "OU=Транспортный цех,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    # Mismatch with applicant department does not prevent auto-selection

    # When matching, it provides applicant_department_support
    res_match = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="Бухгалтерия",
        routing_context=ctx,
        cached_ous=mock_cached_ous,
    )
    assert "applicant_department_support" in res_match.evidence


# ---------------------------------------------------------------------------
# 7. Branch hint внутри подтвержденной организации
# ---------------------------------------------------------------------------

def test_branch_hint_within_confirmed_organization(sample_routing_config, mock_cached_ous):
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )
    # Organization is confirmed (either company or category 57), and category 44 provides branch_hint
    task = TaskDTO(
        id=5,
        service_id=53,
        name="Тест",
        categories='57: АО "НТЗ "ТЭМ-ПО" || 44: Филиал в г.Москва',
    )
    ctx = build_ou_routing_context(task, routing_config=sample_routing_config)

    res = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="Бухгалтерия",
        routing_context=ctx,
        cached_ous=mock_cached_ous,
    )
    # Should resolve to the branch OU in Moscow, not the central one
    assert res.target_ou == "OU=Бухгалтерия,OU=Филиал в г.Москва,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    assert "branch_mapping" in res.evidence


# ---------------------------------------------------------------------------
# 8. Unknown / review-required category
# ---------------------------------------------------------------------------

def test_review_required_and_unknown_category(sample_routing_config, mock_cached_ous):
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )

    # 1. Category 70 has review_required mode
    task_rev = TaskDTO(
        id=6,
        service_id=53,
        name="Тест",
        categories="70: ПТФК ЗТЭО",
    )
    ctx_rev = build_ou_routing_context(task_rev, routing_config=sample_routing_config)
    res_rev = resolver.resolve(
        company=None,
        department="Бухгалтерия",
        routing_context=ctx_rev,
        cached_ous=mock_cached_ous,
    )
    assert res_rev.target_ou is None
    assert res_rev.conflict_reason == "review_required_category"

    # 2. Unknown category id 99999
    task_unk = TaskDTO(
        id=7,
        service_id=53,
        name="Тест",
        categories="99999: Неизвестная категория",
    )
    ctx_unk = build_ou_routing_context(task_unk, routing_config=sample_routing_config)
    res_unk = resolver.resolve(
        company=None,
        department="Бухгалтерия",
        routing_context=ctx_unk,
        cached_ous=mock_cached_ous,
    )
    assert res_unk.target_ou is None
    assert res_unk.resolved_scope_dn is None


# ---------------------------------------------------------------------------
# 9. Department aliases и ограниченный fuzzy внутри scope
# ---------------------------------------------------------------------------

def test_department_aliases_and_bounded_fuzzy(sample_routing_config, mock_cached_ous):
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )

    # Exact alias: "ПШМ АТХ" via alias "АТХ"
    res_alias = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="АТХ",
        routing_context=None,
        cached_ous=mock_cached_ous,
    )
    assert res_alias.target_ou == "OU=ПШМ АТХ,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    assert "department_alias" in res_alias.evidence

    # Fuzzy department match: "Отдел продажи" -> "Отдел продаж" (score >= 0.92, margin >= 0.12)
    res_fuzzy_high = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="Отдел продажи",
        routing_context=None,
        cached_ous=mock_cached_ous,
    )
    assert res_fuzzy_high.target_ou == "OU=Отдел продаж,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    assert "department_similarity" in res_fuzzy_high.evidence

    # Low similarity score or ambiguous margin -> must not auto-select
    res_fuzzy_ambiguous = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="Служба снабжения и логистики",
        routing_context=None,
        cached_ous=mock_cached_ous,
    )
    assert res_fuzzy_ambiguous.target_ou is None
    assert len(res_fuzzy_ambiguous.candidates) > 0


def test_auto_select_confidence_threshold_65_percent(sample_routing_config, mock_cached_ous):
    """Verify that auto-selection threshold activates at 65% (0.65) confidence."""
    from core.ad.ou_resolver import MIN_OU_AUTO_SELECT_CONFIDENCE

    assert MIN_OU_AUTO_SELECT_CONFIDENCE == 0.65

    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )
    # "Маркетинг" has similarity ~0.72 with "Отдел маркетинга" (>= 0.65) and clear margin
    res = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="Маркетинг",
        cached_ous=mock_cached_ous,
    )
    assert res.target_ou == "OU=Отдел маркетинга,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    assert "department_similarity" in res.evidence
    assert res.candidates[0].score >= 0.65


def test_auto_select_highest_confidence_when_candidates_close(sample_routing_config):
    """Verify that highest confidence candidate is selected even if runner-up is very close (< 5%)."""
    close_ous = [
        "OU=ДУиДМ,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "OU=ОУиДГП,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "OU=ОТиЗ,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
    ]
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )
    # "ОУиДМ": ДУиДМ (0.80) vs ОУиДГП (0.7273)
    res = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="ОУиДМ",
        cached_ous=close_ous,
    )
    assert res.target_ou == "OU=ДУиДМ,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    assert res.method == "department_similarity"
    assert res.candidates[0].name == "ДУиДМ"


# ---------------------------------------------------------------------------
# 10. Максимум пять безопасных кандидатов для HITL
# ---------------------------------------------------------------------------

def test_max_five_safe_candidates_for_hitl(sample_routing_config):
    # Scope with 10 OUs
    many_ous = [
        f"OU=Цех {i},OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
        for i in range(1, 11)
    ]
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )
    res = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="Несуществующий цех",
        routing_context=None,
        cached_ous=many_ous,
    )
    assert res.target_ou is None
    assert len(res.candidates) <= 5


# ---------------------------------------------------------------------------
# 11. Запрет внешних и служебных OU
# ---------------------------------------------------------------------------

def test_prohibition_of_external_and_system_ous(sample_routing_config, mock_cached_ous):
    resolver = AdOuResolver(
        users_root_ou="OU=Предприятия,DC=corp,DC=tempo",
        routing_config=sample_routing_config,
    )
    # Search for an account or disabled OU name
    res = resolver.resolve(
        company='АО "НТЗ "ТЭМ-ПО"',
        department="Disabled Users",
        routing_context=None,
        cached_ous=mock_cached_ous,
    )
    # Service Accounts and Disabled Users must NEVER be selected or suggested
    assert res.target_ou != "OU=Disabled Users,DC=corp,DC=tempo"
    assert "OU=Disabled Users,DC=corp,DC=tempo" not in res.candidates
    assert "OU=Service Accounts,DC=corp,DC=tempo" not in res.candidates


# ---------------------------------------------------------------------------
# 12. Изменение YAML hash после preflight -> блокировка исполнения
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_yaml_hash_change_blocks_execution(monkeypatch):
    mock_session = AsyncMock()

    # Preflight record created with old_hash
    preflight_rec = MagicMock()
    preflight_rec.status = "passed"
    preflight_rec.expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    preflight_rec.snapshot_hash = "snap_123"
    preflight_rec.plan_hash = "plan_123"
    preflight_rec.params_hash = "params_123"
    preflight_rec.details_json = {
        "target_ou": "OU=Бухгалтерия,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo",
        "routing_policy_hash": "old_policy_hash_111",
        "routing_policy_version": 1,
    }
    mock_session.get.return_value = preflight_rec
    mock_session.scalar.return_value = preflight_rec

    action = MagicMock()
    action.id = "act_1"
    action.capability_key = "create_ad_user"
    action.params = {"ou_routing_policy_hash": "old_policy_hash_111"}

    plan = MagicMock()
    plan.id = "plan_1"
    plan.snapshot_hash = "snap_123"
    plan.plan_hash = "plan_123"

    command = MagicMock()
    command.execution_permit_id = None
    command.action_plan_id = "plan_1"
    command.task_id = 100

    snapshot = MagicMock()
    snapshot.snapshot_hash = "snap_123"

    monkeypatch.setattr(
        "worker.src.tasks.command_dispatcher.canonical_params_hash",
        lambda p: "params_123",
    )

    # Current config has new_hash
    monkeypatch.setattr(
        "core.ad.ou_routing.get_cached_routing_config",
        lambda: (MagicMock(), "new_policy_hash_222", 2),
    )

    err, target_ou = await _validate_mutation_preflight(
        session=mock_session,
        plan=plan,
        action=action,
        command=command,
        current_snapshot=snapshot,
    )
    assert err == "routing_policy_hash_mismatch"
    assert target_ou is None


# ---------------------------------------------------------------------------
# 13. Равенство preflight OU и фактически исполненного OU
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_target_ou_enforced_in_dispatcher(monkeypatch):
    mock_session = AsyncMock()

    certified_ou = "OU=Бухгалтерия,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    preflight_rec = MagicMock()
    preflight_rec.status = "passed"
    preflight_rec.expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    preflight_rec.snapshot_hash = "snap_123"
    preflight_rec.plan_hash = "plan_123"
    preflight_rec.params_hash = "params_123"
    preflight_rec.details_json = {
        "target_ou": certified_ou,
        "routing_policy_hash": "policy_hash_match",
        "routing_policy_version": 1,
    }
    mock_session.get.return_value = preflight_rec
    mock_session.scalar.return_value = preflight_rec

    action = MagicMock()
    action.id = "act_1"
    action.capability_key = "create_ad_user"
    action.params = {
        "ou_routing_policy_hash": "policy_hash_match",
        "target_ou_dn": "OU=Other,DC=corp,DC=tempo",  # Divergent attempt
    }

    plan = MagicMock()
    plan.id = "plan_1"
    plan.snapshot_hash = "snap_123"
    plan.plan_hash = "plan_123"

    command = MagicMock()
    command.execution_permit_id = None
    command.action_plan_id = "plan_1"
    command.task_id = 100

    snapshot = MagicMock()
    snapshot.snapshot_hash = "snap_123"

    monkeypatch.setenv("AD_USERS_OU", "OU=Предприятия,DC=corp,DC=tempo")
    monkeypatch.setattr(
        "worker.src.tasks.command_dispatcher.canonical_params_hash",
        lambda p: "params_123",
    )
    monkeypatch.setattr(
        "core.ad.ou_routing.get_cached_routing_config",
        lambda: (MagicMock(), "policy_hash_match", 1),
    )

    err, server_target_ou = await _validate_mutation_preflight(
        session=mock_session,
        plan=plan,
        action=action,
        command=command,
        current_snapshot=snapshot,
    )
    assert err is None
    assert server_target_ou == certified_ou


# ---------------------------------------------------------------------------
# 14. Отсутствие routing-контекста в LLM payload
# ---------------------------------------------------------------------------

def test_ou_routing_context_not_in_llm_payload(sample_routing_config):
    task = TaskDTO(
        id=142536,
        service_id=53,
        name="Создание учетной записи сотрудника",
        description="Создать учетную запись для нового сотрудника Сидорова С.С.",
        categories='57: АО "НТЗ "ТЭМ-ПО" || 44: Филиал в г.Москва',
        creator_company_name='АО "НТЗ "ТЭМ-ПО"',
        creator_department="Бухгалтерия",
    )
    routing_ctx = build_ou_routing_context(task, routing_config=sample_routing_config)
    snapshot = TicketSnapshotFactory.create(
        task=task,
        ou_routing_context=routing_ctx,
    )

    extractor = CaseFrameExtractor(model_alias="mock")
    # Inspect sanitized sources prepared for LLM
    sources = extractor._sanitized_sources(snapshot)

    # Convert all sources to string to ensure no routing leaks
    combined_sources_text = " ".join(sources.values())

    # Assert that sensitive routing metadata is NOT present in LLM sources
    assert "ou_routing_context" not in sources
    assert "routing_policy_hash" not in combined_sources_text
    assert "ntz_tempo" not in combined_sources_text


# ---------------------------------------------------------------------------
# 15. CI Gate: Валидация рабочей конфигурации ad_ou_routing.yaml
# ---------------------------------------------------------------------------

def test_production_routing_config_validation():
    """Verify that the repository's production ad_ou_routing.yaml passes full validation."""
    from core.ad.ou_routing import ROUTING_CONFIG_PATH, AdOuRoutingValidator, load_routing_config

    assert ROUTING_CONFIG_PATH.exists(), f"Production routing config file missing at {ROUTING_CONFIG_PATH}"
    cfg, cfg_hash, version = load_routing_config(ROUTING_CONFIG_PATH)

    validator = AdOuRoutingValidator(config=cfg)
    errors = validator.validate()
    assert errors == [], f"Production ad_ou_routing.yaml has validation errors: {errors}"


def test_cli_main_validation_runner(monkeypatch):
    """Verify CLI entrypoint returns exit code 0 on production config."""
    from core.ad.ou_routing import main

    monkeypatch.setattr("sys.argv", ["ou_routing.py"])
    exit_code = main()
    assert exit_code == 0


# ---------------------------------------------------------------------------
# 16. P1/P2 Regression Gates: Fail-Closed Company, Branch Narrowing & Live CLI
# ---------------------------------------------------------------------------

def test_unmapped_employee_company_fails_closed_without_applicant_fallback(sample_routing_config, mock_cached_ous):
    """[P1] Unknown/misspelled employee company must NOT fallback to applicant company."""
    resolver = AdOuResolver(config=sample_routing_config)
    res = resolver.resolve(
        units=mock_cached_ous,
        company="НЕИЗВЕСТНАЯ КОМПАНИЯ",
        department="Бухгалтерия",
        applicant_company='АО "НТЗ "ТЭМ-ПО"',
        applicant_department="Бухгалтерия",
    )
    assert res.selected_ou is None
    assert res.conflict_reason == "target_company_unresolved"


def test_unresolved_branch_hint_fails_closed_without_central_fallback(sample_routing_config, mock_cached_ous):
    """[P1] Unresolved branch hint must NOT fall back to central organization department."""
    resolver = AdOuResolver(config=sample_routing_config)
    # tk_tempo only has central "Бухгалтерия" in mock_cached_ous, no Moscow branch
    res = resolver.resolve(
        units=mock_cached_ous,
        company="ООО ТК ТЭМПО",
        department="Бухгалтерия",
        categories="44: Филиал в г.Москва",
    )
    assert res.selected_ou is None
    assert res.conflict_reason == "branch_hint_unresolved"


def test_resolved_branch_hint_narrows_strictly_to_branch_department(sample_routing_config, mock_cached_ous):
    """[P1] Resolved branch hint must select branch department, not central department."""
    resolver = AdOuResolver(config=sample_routing_config)
    # ntz_tempo has both central "OU=Бухгалтерия,OU=АО НТЗ ТЭМПО..." and branch "OU=Бухгалтерия,OU=Филиал в г.Москва..."
    res = resolver.resolve(
        units=mock_cached_ous,
        company="АО НТЗ ТЭМПО",
        department="Бухгалтерия",
        categories="44: Филиал в г.Москва",
    )
    assert res.selected_ou == "OU=Бухгалтерия,OU=Филиал в г.Москва,OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"
    assert "branch_mapping" in res.evidence


def test_cli_main_check_ad_success(monkeypatch):
    """[P2] Verify --check-ad flag succeeds with valid live AD units."""
    from core.ad.credentials import AD_USERS_ROOT_OU
    from core.ad.ou_resolver import OrganizationalUnit
    from core.ad.ou_routing import ROUTING_CONFIG_PATH, load_routing_config, main

    cfg, _, _ = load_routing_config(ROUTING_CONFIG_PATH)
    units = [
        OrganizationalUnit(name="CORPORATE_USERS", distinguished_name=AD_USERS_ROOT_OU),
    ]
    for org in cfg.organizations.values():
        units.append(OrganizationalUnit(name="org", distinguished_name=org.scope_dn))
        for dept in org.departments.values():
            units.append(OrganizationalUnit(name="dept", distinguished_name=f"{dept.target_relative_dn},{org.scope_dn}"))

    async def mock_fetch_ad(users_root_ou=None):
        return units

    monkeypatch.setattr("core.ad.ou_routing.fetch_live_ad_units", mock_fetch_ad)
    exit_code = main(["--check-ad"])
    assert exit_code == 0


def test_cli_main_check_ad_error(monkeypatch):
    """[P2] Verify --check-ad flag handles connection error and returns 1."""
    from core.ad.ou_routing import main

    async def mock_fetch_ad(users_root_ou=None):
        raise ConnectionError("AD unavailable")

    monkeypatch.setattr("core.ad.ou_routing.fetch_live_ad_units", mock_fetch_ad)
    exit_code = main(["--check-ad"])
    assert exit_code == 1


def test_cli_main_check_is_success(monkeypatch):
    """[P2] Verify --check-is flag succeeds with valid live IntraService categories."""
    from core.ad.ou_routing import ROUTING_CONFIG_PATH, load_routing_config, main

    cfg, _, _ = load_routing_config(ROUTING_CONFIG_PATH)
    service_id = 53
    rule = cfg.services[service_id]
    mock_cats = [
        TaskCategoryDTO(id=cid, name=crule.expected_names[0] if crule.expected_names else f"Cat {cid}")
        for cid, crule in rule.categories.items()
    ]

    async def mock_fetch_is(sid):
        return mock_cats

    monkeypatch.setattr("core.ad.ou_routing.fetch_live_service_categories", mock_fetch_is)
    exit_code = main(["--check-is", "--service-id", "53"])
    assert exit_code == 0


def test_cli_main_check_is_error(monkeypatch):
    """[P2] Verify --check-is flag handles API error and returns 1."""
    from core.ad.ou_routing import main

    async def mock_fetch_is(sid):
        raise RuntimeError("IntraService API error")

    monkeypatch.setattr("core.ad.ou_routing.fetch_live_service_categories", mock_fetch_is)
    exit_code = main(["--check-is"])
    assert exit_code == 1


@pytest.mark.asyncio
async def test_live_ad_units_adapter_with_pool(monkeypatch):
    """[P2] Verify fetch_live_ad_units live adapter functionality with mocked pool."""
    from core.ad.ou_routing import fetch_live_ad_units

    class FakeEntry:
        def __init__(self, name, dn):
            self.ou = MagicMock(value=name)
            self.distinguishedName = MagicMock(value=dn)

    fake_conn = MagicMock()
    fake_conn.entries = [
        FakeEntry("Предприятия", "OU=Предприятия,DC=corp,DC=tempo"),
        FakeEntry("АО НТЗ ТЭМПО", "OU=АО НТЗ ТЭМПО,OU=Предприятия,DC=corp,DC=tempo"),
    ]

    class FakeContext:
        def __enter__(self):
            return fake_conn
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    fake_pool = MagicMock()
    fake_pool.connection_scope.return_value = FakeContext()

    monkeypatch.setattr("core.ad.pool.get_default_ad_pool", lambda: fake_pool)
    # Force vault load failure to test fallback to get_default_ad_pool
    monkeypatch.setattr(
        "core.ad.credentials.ActiveDirectoryCredentialVault.load",
        AsyncMock(side_effect=RuntimeError("no db")),
    )

    res = await fetch_live_ad_units("OU=Предприятия,DC=corp,DC=tempo")
    assert len(res) == 2
    assert res[0].name == "Предприятия"
    assert res[1].name == "АО НТЗ ТЭМПО"


@pytest.mark.asyncio
async def test_live_service_categories_adapter(monkeypatch):
    """[P2] Verify fetch_live_service_categories live adapter functionality."""
    from core.ad.ou_routing import fetch_live_service_categories
    from core.intraservice.auth import ServiceAuthCredentials

    mock_creds = ServiceAuthCredentials(auth_b64="dGVzdA==", bot_user_id=1, login="bot")
    monkeypatch.setattr(
        "core.intraservice.auth.ServiceAuthBootstrap.bootstrap_auth",
        AsyncMock(return_value=mock_creds),
    )
    mock_cats = [TaskCategoryDTO(id=57, name="АО НТЗ ТЭМПО")]
    monkeypatch.setattr(
        "core.intraservice.client.IntraServiceClient.get_categories",
        AsyncMock(return_value=mock_cats),
    )

    res = await fetch_live_service_categories(53)
    assert len(res) == 1
    assert res[0].id == 57
    assert res[0].name == "АО НТЗ ТЭМПО"
