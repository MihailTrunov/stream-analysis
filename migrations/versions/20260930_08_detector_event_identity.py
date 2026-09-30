"""Give every persisted lifecycle transition a storage event UUID.

Revision ID: 20260930_08
Revises: 20260930_07
"""

from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "20260930_08"
down_revision = "20260930_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pattern_instance_transitions",
        sa.Column("event_id", sa.String(36), nullable=True),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT instance_id, sequence FROM pattern_instance_transitions")
    ).all()
    for instance_id, sequence in rows:
        connection.execute(
            sa.text(
                "UPDATE pattern_instance_transitions SET event_id = :event_id "
                "WHERE instance_id = :instance_id AND sequence = :sequence"
            ),
            {"event_id": str(uuid4()), "instance_id": instance_id, "sequence": sequence},
        )
    with op.batch_alter_table("pattern_instance_transitions") as batch:
        batch.alter_column("event_id", existing_type=sa.String(36), nullable=False)
        batch.create_unique_constraint("uq_detector_event_id", ["event_id"])
    if connection.dialect.name == "postgresql":
        op.execute(sa.text(
            "CREATE FUNCTION reject_detector_event_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'detector events are immutable'; END; $$"
        ))
        op.execute(sa.text(
            "CREATE TRIGGER detector_event_immutable BEFORE UPDATE OR DELETE "
            "ON pattern_instance_transitions FOR EACH ROW "
            "EXECUTE FUNCTION reject_detector_event_mutation()"
        ))
    elif connection.dialect.name == "sqlite":
        op.execute(sa.text(
            "CREATE TRIGGER detector_event_immutable_update "
            "BEFORE UPDATE ON pattern_instance_transitions "
            "BEGIN SELECT RAISE(ABORT, 'detector events are immutable'); END"
        ))
        op.execute(sa.text(
            "CREATE TRIGGER detector_event_immutable_delete "
            "BEFORE DELETE ON pattern_instance_transitions "
            "BEGIN SELECT RAISE(ABORT, 'detector events are immutable'); END"
        ))


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute(sa.text("DROP TRIGGER detector_event_immutable ON pattern_instance_transitions"))
        op.execute(sa.text("DROP FUNCTION reject_detector_event_mutation()"))
    elif connection.dialect.name == "sqlite":
        op.execute(sa.text("DROP TRIGGER detector_event_immutable_update"))
        op.execute(sa.text("DROP TRIGGER detector_event_immutable_delete"))
    with op.batch_alter_table("pattern_instance_transitions") as batch:
        batch.drop_constraint("uq_detector_event_id", type_="unique")
        batch.drop_column("event_id")
