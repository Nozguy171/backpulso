from flask import request
from ..models import User

def get_visible_user_id(claims, actor_user_id: int) -> int:
    tenant_id = claims.get("tenant_id")
    role = claims.get("role")

    acting_as = request.headers.get("X-Acting-As-User")
    if role != "leader" or not acting_as:
        return actor_user_id

    try:
        acting_as_id = int(acting_as)
    except Exception:
        return actor_user_id

    u = User.query.filter_by(id=acting_as_id, tenant_id=tenant_id).first()
    return acting_as_id if u else actor_user_id