"""add peripheral_clarify policy and template, adjust wrong_service phrasing

Revision ID: 20260912_0016
Revises: 20260912_0015
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260912_0016"
down_revision: Union[str, None] = "20260912_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add peripheral_clarify template if not exists
    tpl_text = (
        "Здравствуйте! Для работы с периферийным устройством, пожалуйста, "
        "укажите имя или инвентарный номер рабочего компьютера ответным комментарием к этой заявке."
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO response_templates
                (key, version, name, template_text, required_variables, is_active, created_by)
            SELECT
                'peripheral_clarify', 1, 'Запрос имени ПК для работы с периферией',
                '{tpl_text}', '[]', true, 'system:migration'
            WHERE NOT EXISTS (
                SELECT 1 FROM response_templates WHERE key = 'peripheral_clarify' AND version = 1
            )
            """
        )
    )

    # 2. Add peripheral_clarify resolution policy if not exists
    op.execute(
        sa.text(
            """
            INSERT INTO resolution_policies
                (outcome_key, version, outcome_kind, template_id, target_status_id,
                 status_name, expenses, action_id, risk_level, requires_approval,
                 is_active, created_by)
            SELECT
                'peripheral_clarify', 1, 'clarification', rt.id, 35, 'Требует уточнения',
                5, NULL, 0, false, true, 'system:migration'
            FROM response_templates rt
            WHERE rt.key = 'peripheral_clarify' AND rt.is_active = true
              AND NOT EXISTS (
                  SELECT 1 FROM resolution_policies WHERE outcome_key = 'peripheral_clarify' AND version = 1
              )
            LIMIT 1
            """
        )
    )

    # 3. Soften wrong_service template phrasing: proposal rather than claim of completion
    op.execute(
        sa.text(
            """
            UPDATE response_templates
            SET template_text = 'Для решения вопроса требуется обращение в разделе: {{ target_service }}.\\nЕсли у вас остались вопросы, пожалуйста, напишите в комментариях к этой заявке.'
            WHERE key = 'wrong_service' AND template_text LIKE '%Заявка отменена%'
            """
        )
    )


def downgrade() -> None:
    # Revert wrong_service phrasing
    op.execute(
        sa.text(
            """
            UPDATE response_templates
            SET template_text = 'Заявка отменена, т. к. создана не в подходящем разделе.\\nТребуется оставить заявку в подходящем разделе: {{ target_service }}.\\nЕсли у вас остались вопросы, пожалуйста, напишите в комментариях к этой заявке.'
            WHERE key = 'wrong_service' AND template_text LIKE '%Для решения вопроса требуется обращение%'
            """
        )
    )

    # Remove peripheral_clarify policy
    op.execute(
        sa.text(
            """
            DELETE FROM resolution_policies
            WHERE outcome_key = 'peripheral_clarify' AND created_by = 'system:migration'
            """
        )
    )

    # Remove peripheral_clarify template
    op.execute(
        sa.text(
            """
            DELETE FROM response_templates
            WHERE key = 'peripheral_clarify' AND created_by = 'system:migration'
            """
        )
    )
