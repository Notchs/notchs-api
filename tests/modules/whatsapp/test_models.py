from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db.models import Base
from app.modules.whatsapp.models import (
    MessageDirection,
    MessageStatus,
    MessageType,
    WhatsAppMessage,
)

SHEET_COLUMNS = {
    "id": False,
    "wa_message_id": False,
    "direction": False,
    "phone_e164": True,  # wiped when the customer deletes their account
    "user_id": True,
    "message_type": False,
    "template_name": True,
    "body": True,
    "payload": True,
    "status": False,
    "error_code": True,
    "processed_at": True,  # added for the retry sweep
    # Schema review (0012): the reply link, Meta's error title, delivery times.
    "in_reply_to_id": True,
    "error_title": True,
    "delivered_at": True,
    "read_at": True,
    "created_at": False,
    "updated_at": False,
}


def test_registered_for_alembic() -> None:
    assert "whatsapp_messages" in Base.metadata.tables


def test_columns_and_nullability_match_the_sheet() -> None:
    columns = {c.name: c.nullable for c in WhatsAppMessage.__table__.columns}

    assert columns == SHEET_COLUMNS


def test_meta_id_is_unique_and_user_link_survives_deletion() -> None:
    table = WhatsAppMessage.__table__
    uniques = {
        tuple(c.name for c in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    (user_fk,) = table.c.user_id.foreign_keys

    assert ("wa_message_id",) in uniques
    assert user_fk.target_fullname == "users.id" and user_fk.ondelete == "SET NULL"


def test_checks_list_every_enum_value() -> None:
    checks = {
        c.name: str(c.sqltext)
        for c in WhatsAppMessage.__table__.constraints
        if isinstance(c, CheckConstraint)
    }

    for enum, name in (
        (MessageDirection, "ck_whatsapp_messages_direction_valid"),
        (MessageType, "ck_whatsapp_messages_message_type_valid"),
        (MessageStatus, "ck_whatsapp_messages_status_valid"),
    ):
        for member in enum:
            assert f"'{member.value}'" in checks[name]


def test_unprocessed_index_is_partial() -> None:
    (index,) = [
        i for i in WhatsAppMessage.__table__.indexes if i.name == "ix_whatsapp_messages_unprocessed"
    ]

    assert "processed_at IS NULL" in str(index.dialect_options["postgresql"]["where"])
