"""fix clarification outcome kinds and add missing in-progress policies

Revision ID: 20260907_0008
Revises: 20260907_0007
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260907_0008"
down_revision: Union[str, None] = "20260907_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    # 1. Fix outcome_kind for clarification policies in resolution_policies
    op.execute(
        sa.text(
            """
            UPDATE resolution_policies
            SET outcome_kind = 'clarification'
            WHERE outcome_key IN (
                'pc_offline',
                'printer_offline',
                'printer_ip_clarify',
                'file_lock_smb',
                'anydesk_fallback_assistant',
                'call_failed',
                'account_pc_occupied'
            )
            """
        )
    )

    # 2. Add missing response_templates if not exist
    new_templates = [
        (
            "file_lock_smb_in_progress",
            "Сброс блокировки файла 1С / SMB",
            "Добрый день! Ваша заявка принята в работу. Выполняем поиск и сброс зависшей файловой сессии на сервере.",
            "[]",
        ),
        (
            "rag_historical_solution",
            "Применение решения из базы знаний",
            "Заявка принята в работу. Для аналогичной проблемы ранее применялось решение: {{ solution }}. Проверяем применимость к вашей заявке.",
            '["solution"]',
        ),
        (
            "downtime_priority",
            "Приоритет: Риск производственного простоя",
            "Заявка принята в наивысшем приоритете в связи с риском производственного простоя. Инженер 1-й линии немедленно приступил к локализации инцидента.",
            "[]",
        ),
    ]

    for key, name, text_tpl, req_vars in new_templates:
        op.execute(
            sa.text(
                f"""
                INSERT INTO response_templates
                    (key, version, name, template_text, required_variables, is_active, created_by)
                SELECT
                    '{key}', 1, '{name}', '{text_tpl}', '{req_vars}', true, 'system:migration'
                WHERE NOT EXISTS (
                    SELECT 1 FROM response_templates WHERE key = '{key}' AND version = 1
                )
                """
            )
        )

    # 3. Add corresponding resolution_policies
    new_policies = [
        ("file_lock_smb_in_progress", "resolution", "file_lock_smb_in_progress", 27, "В работе", 10),
        ("rag_historical_solution", "resolution", "rag_historical_solution", 27, "В работе", 10),
        ("downtime_priority", "resolution", "downtime_priority", 27, "В работе", 15),
    ]

    for outcome_key, outcome_kind, tpl_key, status_id, status_name, expenses in new_policies:
        op.execute(
            sa.text(
                f"""
                INSERT INTO resolution_policies
                    (outcome_key, version, outcome_kind, template_id, target_status_id,
                     status_name, expenses, action_id, risk_level, requires_approval,
                     is_active, created_by)
                SELECT
                    '{outcome_key}', 1, '{outcome_kind}', rt.id, {status_id}, '{status_name}',
                    {expenses}, NULL, 0, false, true, 'system:migration'
                FROM response_templates rt
                WHERE rt.key = '{tpl_key}' AND rt.is_active = true
                  AND NOT EXISTS (
                      SELECT 1 FROM resolution_policies WHERE outcome_key = '{outcome_key}' AND version = 1
                  )
                """
            )
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM resolution_policies
            WHERE outcome_key IN ('file_lock_smb_in_progress', 'rag_historical_solution', 'downtime_priority');
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM response_templates
            WHERE key IN ('file_lock_smb_in_progress', 'rag_historical_solution', 'downtime_priority');
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE resolution_policies
            SET outcome_kind = 'resolution'
            WHERE outcome_key IN (
                'pc_offline',
                'printer_offline',
                'printer_ip_clarify',
                'file_lock_smb',
                'anydesk_fallback_assistant',
                'call_failed',
                'account_pc_occupied'
            );
            """
        )
    )
