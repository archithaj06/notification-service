from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.template import Template


class TemplateRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_name(self, name: str) -> Template | None:
        stmt = select(Template).where(Template.name == name)
        return self.db.execute(stmt).scalar_one_or_none()

    def create(self, template: Template) -> Template:
        self.db.add(template)
        self.db.flush()
        return template
