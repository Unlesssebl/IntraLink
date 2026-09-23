"""IntraService API typed client and domain DTOs."""

from core.intraservice.catalog import ServiceCatalog
from core.intraservice.client import IntraServiceClient
from core.intraservice.dto import (
    AttachmentDTO,
    ExtractedEntitiesDTO,
    ServiceDTO,
    TaskDTO,
    TaskLifetimeEventDTO,
    TaskStatusDTO,
    TaskTypeDTO,
)
from core.intraservice.exceptions import (
    CircuitBreakerOpenError,
    IntraServiceAuthError,
    IntraServiceError,
    IntraServiceNotFoundError,
    IntraServiceServerError,
    IntraServiceValidationError,
)
from core.intraservice.parser import enrich_task_dict, parse_custom_fields

__all__ = [
    "IntraServiceClient",
    "ServiceCatalog",
    "TaskDTO",
    "ServiceDTO",
    "TaskLifetimeEventDTO",
    "TaskStatusDTO",
    "TaskTypeDTO",
    "AttachmentDTO",
    "ExtractedEntitiesDTO",
    "parse_custom_fields",
    "enrich_task_dict",
    "IntraServiceError",
    "IntraServiceAuthError",
    "IntraServiceNotFoundError",
    "IntraServiceValidationError",
    "IntraServiceServerError",
    "CircuitBreakerOpenError",
]
