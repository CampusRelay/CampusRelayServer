"""add authentication

Revision ID: cf86ae7d0d00
Revises: 486dc737984b
Create Date: 2026-11-15 10:41:38.404408

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'cf86ae7d0d00'
down_revision: Union[str, Sequence[str], None] = '486dc737984b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('username', sa.String(length=32), nullable=True)
    )

    op.execute(
        sa.text(
            "UPDATE users SET username = 'user_' || id::text "
            "WHERE username IS NULL"
        )
    )

    op.alter_column(
        'users',
        'username',
        existing_type=sa.String(length=32),
        nullable=False
    )

    op.alter_column(
        'users',
        'email',
        existing_type=sa.VARCHAR(length=255),
        nullable=True
    )

    op.create_unique_constraint(
        'uq_users_username',
        'users',
        ['username']
    )

    op.create_table(
        'platform_admins',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column(
            'is_active',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('user_id')
    )

    op.create_table(
        'user_sessions',
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('active_school_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ['active_school_id'], ['schools.id'], ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('session_id'),
        sa.UniqueConstraint('token_hash')
    )


def downgrade() -> None:
    op.drop_table('user_sessions')
    op.drop_table('platform_admins')

    op.drop_constraint(
        'uq_users_username',
        'users',
        type_='unique'
    )

    op.alter_column(
        'users',
        'email',
        existing_type=sa.VARCHAR(length=255),
        nullable=False
    )

    op.drop_column('users', 'username')
