"""add_super_agent_type_agents_table

Revision ID: a1b2c3d4e5f6
Revises: 2df073c7b564
Create Date: 2026-03-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "2df073c7b564"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("check_agent_type", "agents", type_="check")
    op.create_check_constraint(
        "check_agent_type",
        "agents",
        "type IN ('llm', 'sequential', 'parallel', 'loop', 'a2a', 'workflow', 'crew_ai', 'task', 'super')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("check_agent_type", "agents", type_="check")
    op.create_check_constraint(
        "check_agent_type",
        "agents",
        "type IN ('llm', 'sequential', 'parallel', 'loop', 'a2a', 'workflow', 'crew_ai', 'task')",
    )
