"""Pydantic schemas for Reports feature slice."""

from typing import List, Optional

from pydantic import BaseModel


class EngineerLoadMetricDTO(BaseModel):
    user_id: int
    user_name: str
    total_assigned: int
    total_closed: int
    avg_resolution_hours: float
    reopened_count: int


class MonthlyLoadReportDTO(BaseModel):
    year: int
    month: int
    is_closed_month: bool
    total_tickets: int
    engineers: List[EngineerLoadMetricDTO]
    cached: bool = False


class ReportExportResponse(BaseModel):
    year: int
    month: int
    format: str  # 'csv', 'json'
    download_url: Optional[str] = None
    data: Optional[str] = None
