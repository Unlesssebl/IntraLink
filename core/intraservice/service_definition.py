"""Pydantic v2 declarative schema definitions for IntraService service catalog contracts."""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class ServiceDefinition(BaseModel):
    """Canonical contract for an IntraService catalog service or service group.

    Governs facts extraction, host reachability requirements, dialogue prompts
    and autonomous execution routing.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    service_ids: List[int] = Field(
        ...,
        description="Supported IntraService Service IDs (e.g. [55, 232])",
    )
    name: str = Field(
        ...,
        description="Human-readable service name (e.g. 'Создание учетной записи Directum / AD')",
    )
    required_facts: List[str] = Field(
        default_factory=list,
        description="Required domain entity keys: ['first_name', 'last_name', 'department']",
    )
    requires_online_host: bool = Field(
        default=False,
        description="Whether applicant's workstation must be online and responsive",
    )
    probe_ports: List[int] = Field(
        default_factory=list,
        description="Diagnostic TCP ports to probe (e.g. [5985, 9100, 445])",
    )
    clarification_template: str = Field(
        default="",
        description="Standard dialogue prompt template sent to applicant when facts or host are missing",
    )
    adapter_key: str = Field(
        ...,
        description="Key of the matching execution adapter (e.g. 'account_create', 'install_printer')",
    )
    min_confidence: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Minimum router confidence score threshold required for FULL_AUTO autonomy",
    )
    description: Optional[str] = Field(
        default=None,
        description="Detailed description and operational guidelines for helpdesk staff",
    )
