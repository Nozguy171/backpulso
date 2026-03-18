from flask import request
from ..models import User

def get_visible_user_id(claims, actor_user_id: int) -> int:
    role = (claims or {}).get("role")
    tenant_id = (claims or {}).get("tenant_id")

    acting_as = request.headers.get("X-Acting-As-User")
    if not acting_as:
        return actor_user_id

    if role not in ("leader", "admin"):
        return actor_user_id

    try:
        acting_as_id = int(acting_as)
    except Exception:
        return actor_user_id

    u = User.query.filter_by(id=acting_as_id, tenant_id=tenant_id).first()
    if not u:
        return actor_user_id

    return acting_as_id