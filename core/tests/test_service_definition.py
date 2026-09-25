"""Tests for ServiceDefinition contract and validation."""

import pytest
from pydantic import ValidationError

from core.intraservice.service_definition import ServiceDefinition


def test_service_definition_valid():
    """Verify ServiceDefinition creates and serializes correctly."""
    definition = ServiceDefinition(
        name="Подключение сетевого принтера",
        service_ids=[42, 105],
        adapter_key="install_printer",
        description="Сценарий автоматической установки сетевого принтера",
        required_facts=["printer_address", "pc_name"],
        requires_online_host=True,
        probe_ports=[9100, 445],
        min_confidence=0.80,
    )
    assert definition.name == "Подключение сетевого принтера"
    assert definition.service_ids == [42, 105]
    assert definition.adapter_key == "install_printer"
    assert definition.requires_online_host is True
    assert definition.probe_ports == [9100, 445]
    assert definition.min_confidence == 0.80

    dump = definition.model_dump()
    assert dump["adapter_key"] == "install_printer"
    assert dump["service_ids"] == [42, 105]


def test_service_definition_defaults():
    """Verify default values in ServiceDefinition."""
    definition = ServiceDefinition(
        name="Создание учетной записи Directum / AD",
        service_ids=[55, 232],
        adapter_key="account_create",
    )
    assert definition.service_ids == [55, 232]
    assert definition.adapter_key == "account_create"
    assert definition.required_facts == []
    assert definition.requires_online_host is False
    assert definition.probe_ports == []
    assert definition.clarification_template == ""
    assert definition.min_confidence == 0.85
    assert definition.description is None
