from ..models import AuditLog


def log(db, user, entity: str, entity_id, action: str, detail: str = ""):
    db.add(AuditLog(user_id=getattr(user, "id", None), entity=entity, entity_id=entity_id, action=action, detail=detail))
