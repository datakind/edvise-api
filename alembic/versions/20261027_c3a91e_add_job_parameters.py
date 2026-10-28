"""add_job_parameters

Revision ID: 20261027_c3a91e
Revises: 20260915_a7f0d0
Create Date: 2026-10-27 15:20:00.000000

Stores the Databricks job_parameters dict sent when an inference run is
triggered. Nullable so existing job rows stay NULL.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261027_c3a91e"
down_revision: Union[str, Sequence[str], None] = "20260915_a7f0d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("job", sa.Column("job_parameters", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("job", "job_parameters")
