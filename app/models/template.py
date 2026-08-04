from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, generate_uuid


class Template(Base, TimestampMixin):
    """
    A reusable message template with {{variable}} placeholders, e.g.:
        "Hello {{name}}, your order {{order_id}} has shipped!"

    Stored in the DB (not in-memory) so templates survive restarts and can be
    managed without a redeploy. `name` is the human-facing key clients use
    when creating a notification (e.g. "order_shipped").
    """

    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)  # used for email
    body: Mapped[str] = mapped_column(Text, nullable=False)
