"""Tests for ActiveDirectoryGroupResolver edge cases and real AD data."""

from unittest.mock import MagicMock

from core.ad.group_resolver import (
    ActiveDirectoryGroup,
    ActiveDirectoryGroupResolver,
)

REAL_AD_GROUPS = [
    ActiveDirectoryGroup("HLP_ООО Айти ТЭМПО", "HLP_ООО Айти ТЭМПО", "CN=HLP_ООО Айти ТЭМПО,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_АО ИТЗ", "HLP_АО ИТЗ", "CN=HLP_АО ИТЗ,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_АО КЗМК ТЭМПО", "HLP_АО КЗМК ТЭМПО", "CN=HLP_АО КЗМК ТЭМПО,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_АО КМК ТЭМПО", "HLP_АО КМК ТЭМПО", "CN=HLP_АО КМК ТЭМПО,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_АО КПК ТЭМПО", "HLP_АО КПК ТЭМПО", "CN=HLP_АО КПК ТЭМПО,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_АО НПО Татэлектромаш", "HLP_АО НПО Татэлектромаш", "CN=HLP_АО НПО Татэлектромаш,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_АО НТЗ ТЭМПО", "HLP_АО НТЗ ТЭМПО", "CN=HLP_АО НТЗ ТЭМПО,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_АО ПТФК ЗТЭО", "HLP_АО ПТФК ЗТЭО", "CN=HLP_АО ПТФК ЗТЭО,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_АО ПТФК Технотрон", "HLP_АО ПТФК Технотрон", "CN=HLP_АО ПТФК Технотрон,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_АО СКС ТЭМПО", "HLP_АО СКС ТЭМПО", "CN=HLP_АО СКС ТЭМПО,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_АО ТОРГОВЫЙ ДОМ ТЭМПО", "HLP_АО ТОРГОВЫЙ ДОМ ТЭМПО", "CN=HLP_АО ТОРГОВЫЙ ДОМ ТЭМПО,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ИП Ахатов ИИ", "HLP_ИП Ахатов ИИ", "CN=HLP_ИП Ахатов ИИ,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ИП Казыханов ЭФ", "HLP_ИП Казыханов ЭФ", "CN=HLP_ИП Казыханов ЭФ,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ИП Нуриева", "HLP_ИП Нуриева", "CN=HLP_ИП Нуриева,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ИП Петров ДВ", "HLP_ИП Петров ДВ", "CN=HLP_ИП Петров ДВ,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ИП Сагдатуллин", "HLP_ИП Сагдатуллин", "CN=HLP_ИП Сагдатуллин,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ИП Шарифуллин", "HLP_ИП Шарифуллин", "CN=HLP_ИП Шарифуллин,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ИП Шарифуллин Рустем", "HLP_ИП Шарифуллин Рустем", "CN=HLP_ИП Шарифуллин Рустем,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО ДЗИЛ", "HLP_ООО ДЗИЛ", "CN=HLP_ООО ДЗИЛ,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО КИЦ", "HLP_ООО КИЦ", "CN=HLP_ООО КИЦ,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО Камский настил", "HLP_ООО Камский настил", "CN=HLP_ООО Камский настил,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО СОВРЕМЕННЫЕ ТЕХНОЛОГИИ", "HLP_ООО СОВРЕМЕННЫЕ ТЕХНОЛОГИИ", "CN=HLP_ООО СОВРЕМЕННЫЕ ТЕХНОЛОГИИ,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО ТОРГОВЫЙ ДОМ ТЭМ", "HLP_ООО ТОРГОВЫЙ ДОМ ТЭМ", "CN=HLP_ООО ТОРГОВЫЙ ДОМ ТЭМ,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО ТЭМПО ВОСТОК", "HLP_ООО ТЭМПО ВОСТОК", "CN=HLP_ООО ТЭМПО ВОСТОК,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО ТЭМПО Логистик", "HLP_ООО ТЭМПО Логистик", "CN=HLP_ООО ТЭМПО Логистик,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО ТЭМПО Метиз", "HLP_ООО ТЭМПО Метиз", "CN=HLP_ООО ТЭМПО Метиз,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО ТЭМПО ПОРТ", "HLP_ООО ТЭМПО ПОРТ", "CN=HLP_ООО ТЭМПО ПОРТ,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО ТЭМПО Персонал", "HLP_ООО ТЭМПО Персонал", "CN=HLP_ООО ТЭМПО Персонал,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО Техника Агро", "HLP_ООО Техника Агро", "CN=HLP_ООО Техника Агро,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_ООО Технотрон Метиз", "HLP_ООО Технотрон Метиз", "CN=HLP_ООО Технотрон Метиз,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
    ActiveDirectoryGroup("HLP_Филиалы организаций", "HLP_Филиалы организаций", "CN=HLP_Филиалы организаций,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc"),
]


def test_resolve_nt_zavod():
    """Тест кейса пользователя: сотрудник АО НТЗ получает группу HLP_АО НТЗ ТЭМПО."""
    res = ActiveDirectoryGroupResolver.resolve_service_group(
        REAL_AD_GROUPS,
        company="АО НТЗ",
    )
    assert res.group is not None
    assert res.group.name == "HLP_АО НТЗ ТЭМПО"
    assert res.confidence >= 0.85


def test_resolve_nt_zavod_with_quotes():
    """Тест кавычек («НТЗ», "НТЗ")."""
    res1 = ActiveDirectoryGroupResolver.resolve_service_group(REAL_AD_GROUPS, company='АО "НТЗ"')
    assert res1.group is not None
    assert res1.group.name == "HLP_АО НТЗ ТЭМПО"

    res2 = ActiveDirectoryGroupResolver.resolve_service_group(REAL_AD_GROUPS, company="АО «НТЗ»")
    assert res2.group is not None
    assert res2.group.name == "HLP_АО НТЗ ТЭМПО"


def test_resolve_ip_with_dotted_initials():
    """Тест точек в инициалах ИП (ИП Казыханов Э.Ф. -> HLP_ИП Казыханов ЭФ)."""
    res = ActiveDirectoryGroupResolver.resolve_service_group(
        REAL_AD_GROUPS,
        company="ИП Казыханов Э.Ф.",
    )
    assert res.group is not None
    assert res.group.name == "HLP_ИП Казыханов ЭФ"


def test_do_not_assign_filialy_organizaciy_group():
    """Сотрудник филиала не должен получать группу HLP_Филиалы организаций."""
    target_ou = "OU=Отдел ИТ,OU=АО НТЗ,OU=Филиалы организаций,OU=CORPORATE_USERS,DC=corporate,DC=loc"
    res = ActiveDirectoryGroupResolver.resolve_service_group(
        REAL_AD_GROUPS,
        company="АО НТЗ",
        target_ou_dn=target_ou,
    )
    assert res.group is not None
    assert res.group.name == "HLP_АО НТЗ ТЭМПО"
    assert res.group.name != "HLP_Филиалы организаций"


def test_resolve_from_target_ou_when_company_empty():
    """Если поле компании пустое, группа определяется из target_ou_dn."""
    target_ou = "OU=Отдел ИТ,OU=АО ИТЗ,OU=CORPORATE_USERS,DC=corporate,DC=loc"
    res = ActiveDirectoryGroupResolver.resolve_service_group(
        REAL_AD_GROUPS,
        company="",
        target_ou_dn=target_ou,
    )
    assert res.group is not None
    assert res.group.name == "HLP_АО ИТЗ"


def test_disambiguate_similar_holding_names():
    """КЗМК vs КМК vs КПК не должны путаться между собой."""
    res_kzmk = ActiveDirectoryGroupResolver.resolve_service_group(REAL_AD_GROUPS, company="АО КЗМК")
    assert res_kzmk.group is not None
    assert res_kzmk.group.name == "HLP_АО КЗМК ТЭМПО"

    res_kmk = ActiveDirectoryGroupResolver.resolve_service_group(REAL_AD_GROUPS, company="АО КМК")
    assert res_kmk.group is not None
    assert res_kmk.group.name == "HLP_АО КМК ТЭМПО"

    res_kpk = ActiveDirectoryGroupResolver.resolve_service_group(REAL_AD_GROUPS, company="АО КПК")
    assert res_kpk.group is not None
    assert res_kpk.group.name == "HLP_АО КПК ТЭМПО"


def test_missing_group_returns_none_softly():
    """Несуществующая организация мягко возвращает None с предупреждением."""
    res = ActiveDirectoryGroupResolver.resolve_service_group(
        REAL_AD_GROUPS,
        company="Неизвестная Компания 999",
    )
    assert res.group is None
    assert res.warning is not None
    assert "не найдена" in res.warning


def test_add_user_to_group_idempotency():
    """Тест обработки ошибки 68 (entryAlreadyExists) при добавлении в группу."""
    conn = MagicMock()
    # MemberOf search returns empty
    conn.entries = []
    # Modify returns result 68 (already member)
    conn.result = {"result": 68, "description": "entryAlreadyExists", "message": "Type or value exists"}

    ok, err = ActiveDirectoryGroupResolver.add_user_to_group_sync(
        conn,
        user_dn="CN=Иванов Иван,OU=ИТ,DC=corporate,DC=loc",
        group_dn="CN=HLP_АО НТЗ ТЭМПО,OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc",
    )
    assert ok is True
    assert err is None
