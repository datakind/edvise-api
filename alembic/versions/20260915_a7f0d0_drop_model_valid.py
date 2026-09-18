"""drop_model_valid

Revision ID: 20260915_a7f0d0
Revises: 20260831_06779e
Create Date: 2026-09-15 13:10:47.452028

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260915_a7f0d0"
down_revision: Union[str, Sequence[str], None] = "20260831_06779e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("model", "valid")


def downgrade() -> None:
    op.add_column("model", sa.Column("valid", sa.Boolean(), nullable=True))
