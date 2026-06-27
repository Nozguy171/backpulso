import json
from datetime import datetime
from functools import wraps

from dateutil.relativedelta import relativedelta
from flask import Blueprint, g, request
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from sqlalchemy import func

from ..extensions import db
from ..models import (
    AdminAudit,
    Appointment,
    CallReminder,
    Prospect,
    ProspectHistory,
    ProspectSale,
    Tenant,
    User,
)

admin_bp = Blueprint("admin", __name__)
COMMISSION_RATE = 0.01


def platform_admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        verify_jwt_in_request()
        user = db.session.get(User, int(get_jwt_identity()))
        if not user or not user.is_platform_admin:
            return {"message": "Acceso exclusivo para administración de plataforma"}, 403
        g.platform_admin = user
        return fn(*args, **kwargs)

    return wrapped


def _period_range(value):
    now = datetime.now()
    current_month = datetime(now.year, now.month, 1)
    try:
        if value and len(value) == 10:
            start = datetime.strptime(value, "%Y-%m-%d")
            return value, start, start + relativedelta(days=1)
        if value and len(value) == 7:
            start = datetime.strptime(value, "%Y-%m")
            return value, start, start + relativedelta(months=1)
    except ValueError:
        pass
    return current_month.strftime("%Y-%m"), current_month, current_month + relativedelta(months=1)


def _pct(num, den):
    return round((num / den) * 100, 2) if den else 0.0


def _iso(value):
    return value.isoformat() if value else None


def _user_payload(user):
    return {
        "id": user.id,
        "nombre": user.username or user.email,
        "username": user.username,
        "email": user.email,
        "telefono": user.numero_telefonico,
        "role": user.role,
    }


def _team_metrics(tenant, start, end):
    # ponytail: V1 favors clear per-team queries; aggregate in SQL when team volume makes this slow.
    users = User.query.filter_by(tenant_id=tenant.id).filter(User.role.in_(["leader", "collaborator"])).order_by(User.created_at.asc()).all()
    leader = next((user for user in users if user.role == "leader"), users[0] if users else None)
    leader_ids = {user.id for user in users if user.role == "leader"}
    sales = ProspectSale.query.filter(
        ProspectSale.tenant_id == tenant.id,
        ProspectSale.created_at >= start,
        ProspectSale.created_at < end,
    ).all()
    prospects_period = Prospect.query.filter(
        Prospect.tenant_id == tenant.id,
        Prospect.created_at >= start,
        Prospect.created_at < end,
    ).count()
    prospects_total = Prospect.query.filter_by(tenant_id=tenant.id).count()

    leader_sales = sum(float(sale.monto_sin_iva or 0) for sale in sales if sale.sold_by_user_id in leader_ids)
    collaborator_sales = sum(float(sale.monto_sin_iva or 0) for sale in sales if sale.sold_by_user_id not in leader_ids)
    total = leader_sales + collaborator_sales
    sales_count = len(sales)
    last_activity = max(
        filter(
            None,
            [
                db.session.query(func.max(Prospect.updated_at)).filter(Prospect.tenant_id == tenant.id).scalar(),
                db.session.query(func.max(ProspectSale.created_at)).filter(ProspectSale.tenant_id == tenant.id).scalar(),
                db.session.query(func.max(ProspectHistory.created_at)).filter(ProspectHistory.tenant_id == tenant.id).scalar(),
            ],
        ),
        default=None,
    )

    return {
        "tenant_id": tenant.id,
        "lider": _user_payload(leader) if leader else None,
        "colaboradores": sum(1 for user in users if user.role == "collaborator"),
        "prospectos": prospects_period,
        "prospectos_totales": prospects_total,
        "ventas_equipo": round(total, 2),
        "comision": round(total * COMMISSION_RATE, 2),
        "ventas_lider": round(leader_sales, 2),
        "ventas_colaboradores": round(collaborator_sales, 2),
        "numero_ventas": sales_count,
        "conversion": _pct(sales_count, prospects_period),
        "ticket_promedio": round(total / sales_count, 2) if sales_count else 0.0,
        "ultima_actividad": _iso(last_activity),
        "busqueda": " ".join(f"{user.username} {user.email} {user.numero_telefonico}" for user in users).lower(),
    }


def _member_metrics(user, start, end, team_total):
    prospects = Prospect.query.filter(
        Prospect.assigned_to_user_id == user.id,
        Prospect.created_at >= start,
        Prospect.created_at < end,
    ).count()
    appointments = Appointment.query.join(Prospect, Prospect.id == Appointment.prospect_id).filter(
        Prospect.assigned_to_user_id == user.id,
        Appointment.created_at >= start,
        Appointment.created_at < end,
    ).count()
    calls = CallReminder.query.join(Prospect, Prospect.id == CallReminder.prospect_id).filter(
        Prospect.assigned_to_user_id == user.id,
        CallReminder.created_at >= start,
        CallReminder.created_at < end,
    ).count()
    sales = ProspectSale.query.filter(
        ProspectSale.sold_by_user_id == user.id,
        ProspectSale.created_at >= start,
        ProspectSale.created_at < end,
    ).all()
    amount = sum(float(sale.monto_sin_iva or 0) for sale in sales)
    last_activity = max(
        filter(
            None,
            [
                db.session.query(func.max(Prospect.updated_at)).filter(Prospect.assigned_to_user_id == user.id).scalar(),
                db.session.query(func.max(ProspectSale.created_at)).filter(ProspectSale.sold_by_user_id == user.id).scalar(),
            ],
        ),
        default=None,
    )
    return {
        **_user_payload(user),
        "prospectos": prospects,
        "citas": appointments,
        "llamadas": calls,
        "numero_ventas": len(sales),
        "monto_vendido": round(amount, 2),
        "porcentaje_equipo": _pct(amount, team_total),
        "conversion": _pct(len(sales), prospects),
        "ticket_promedio": round(amount / len(sales), 2) if sales else 0.0,
        "ultima_actividad": _iso(last_activity),
    }


def _sale_payload(sale):
    sold_by = sale.sold_by_user
    return {
        "id": sale.id,
        "fecha": _iso(sale.created_at),
        "vendio": _user_payload(sold_by),
        "prospecto": {"id": sale.prospect.id, "nombre": sale.prospect.nombre},
        "tipo": sale.tipo_venta,
        "monto_con_iva": float(sale.monto_con_iva or 0),
        "iva": float(sale.iva_monto or 0),
        "monto_sin_iva": float(sale.monto_sin_iva or 0),
        "origen": "cita" if sale.appointment_id else "llamada" if sale.call_id else "manual",
        "capturada_por": _user_payload(sale.created_by_user),
        "usuario_efectivo": _user_payload(sale.effective_user),
        "comision": round(float(sale.monto_sin_iva or 0) * COMMISSION_RATE, 2),
    }


def _audit_payload(row):
    labels = {
        "password_reset": "Contraseña restablecida",
        "team_status_changed": "Estado del equipo actualizado",
    }
    try:
        details = json.loads(row.details or "{}")
    except (TypeError, json.JSONDecodeError):
        details = {}
    if row.action == "password_reset":
        role = "Líder" if details.get("role") == "leader" else "Colaborador"
        detail_label = f"Se cambió la contraseña de {details.get('user', 'un usuario')} ({role})."
    elif row.action == "team_status_changed":
        states = {"active": "Activo", "review": "En revisión", "read_only": "Solo lectura", "suspended": "Suspendido"}
        detail_label = f"Estado anterior: {states.get(details.get('from'), details.get('from', '—'))}. Nuevo estado: {states.get(details.get('to'), details.get('to', '—'))}."
    else:
        detail_label = row.details or "Sin detalle adicional."
    return {
        "id": row.id,
        "admin": _user_payload(row.admin_user),
        "action": row.action,
        "action_label": labels.get(row.action, row.action.replace("_", " ").capitalize()),
        "target_type": row.target_type,
        "target_label": "Usuario" if row.target_type == "user" else "Equipo",
        "target_id": row.target_id,
        "tenant_id": row.tenant_id,
        "details": detail_label,
        "created_at": _iso(row.created_at),
    }


@admin_bp.get("/dashboard")
@platform_admin_required
def dashboard():
    period, start, end = _period_range(request.args.get("period"))
    teams = [_team_metrics(tenant, start, end) for tenant in Tenant.query.order_by(Tenant.created_at.asc()).all()]
    total_sales = sum(team["ventas_equipo"] for team in teams)
    prospect_count = sum(team["prospectos"] for team in teams)
    sales_count = sum(team["numero_ventas"] for team in teams)
    top_team = max(teams, key=lambda team: team["ventas_equipo"], default=None)
    low_conversion = min(teams, key=lambda team: team["conversion"], default=None)
    return {
        "period": period,
        "dashboard": {
            "total_vendido": round(total_sales, 2),
            "comision_estimada": round(total_sales * COMMISSION_RATE, 2),
            "total_lideres": User.query.filter_by(role="leader").count(),
            "total_colaboradores": User.query.filter_by(role="collaborator").count(),
            "prospectos": prospect_count,
            "numero_ventas": sales_count,
            "conversion": _pct(sales_count, prospect_count),
            "ticket_promedio": round(total_sales / sales_count, 2) if sales_count else 0.0,
            "equipo_mas_ventas": top_team,
            "equipo_menor_conversion": low_conversion,
        },
    }, 200


@admin_bp.get("/teams")
@platform_admin_required
def teams():
    period, start, end = _period_range(request.args.get("period"))
    query = (request.args.get("q") or "").strip().lower()
    rows = [_team_metrics(tenant, start, end) for tenant in Tenant.query.order_by(Tenant.created_at.desc()).all()]
    if query:
        rows = [
            row for row in rows
            if query in row["busqueda"]
        ]
    return {"period": period, "teams": rows}, 200


@admin_bp.get("/teams/<int:tenant_id>")
@platform_admin_required
def team_detail(tenant_id):
    period, start, end = _period_range(request.args.get("period"))
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        return {"message": "Equipo no encontrado"}, 404
    team = _team_metrics(tenant, start, end)
    users = User.query.filter_by(tenant_id=tenant_id).filter(User.role.in_(["leader", "collaborator"])).order_by(User.role.desc(), User.username.asc()).all()
    members = [_member_metrics(user, start, end, team["ventas_equipo"]) for user in users]
    sales = ProspectSale.query.filter(
        ProspectSale.tenant_id == tenant_id,
        ProspectSale.created_at >= start,
        ProspectSale.created_at < end,
    ).order_by(ProspectSale.created_at.desc()).limit(500).all()
    audits = AdminAudit.query.filter_by(tenant_id=tenant_id).order_by(AdminAudit.created_at.desc()).limit(100).all()
    prospects = Prospect.query.filter_by(tenant_id=tenant_id).order_by(Prospect.created_at.desc()).limit(2000).all()
    return {
        "period": period,
        "team": team,
        "members": members,
        "sales": [_sale_payload(sale) for sale in sales],
        "audit": [_audit_payload(row) for row in audits],
        "prospects": [_prospect_payload(row) for row in prospects],
    }, 200


@admin_bp.post("/users/<int:user_id>/reset-password")
@platform_admin_required
def reset_password(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return {"message": "Usuario no encontrado"}, 404
    password = (request.get_json() or {}).get("password") or ""
    if len(password) < 8:
        return {"message": "La contraseña debe tener al menos 8 caracteres"}, 400
    user.set_password(password)
    db.session.add(AdminAudit(
        admin_user_id=g.platform_admin.id,
        action="password_reset",
        target_type="user",
        target_id=user.id,
        tenant_id=user.tenant_id,
        details=json.dumps({"user": user.username, "role": user.role}, ensure_ascii=False),
    ))
    db.session.commit()
    return {"ok": True}, 200


@admin_bp.get("/sales")
@platform_admin_required
def sales():
    period, start, end = _period_range(request.args.get("period"))
    query = ProspectSale.query.filter(ProspectSale.created_at >= start, ProspectSale.created_at < end)
    tenant_id = request.args.get("tenant_id", type=int)
    if tenant_id:
        query = query.filter(ProspectSale.tenant_id == tenant_id)
    rows = query.order_by(ProspectSale.created_at.desc()).limit(500).all()
    return {"period": period, "sales": [_sale_payload(sale) for sale in rows]}, 200


@admin_bp.get("/audit")
@platform_admin_required
def audit():
    query = AdminAudit.query
    tenant_id = request.args.get("tenant_id", type=int)
    if tenant_id:
        query = query.filter(AdminAudit.tenant_id == tenant_id)
    rows = query.order_by(AdminAudit.created_at.desc()).limit(300).all()
    return {"audit": [_audit_payload(row) for row in rows]}, 200


@admin_bp.get("/periods")
@platform_admin_required
def periods():
    current = datetime.now().strftime("%Y-%m")
    months = sorted({current, *(sale.created_at.strftime("%Y-%m") for sale in ProspectSale.query.all() if sale.created_at)}, reverse=True)
    return {"months": months, "current": current}, 200


def _prospect_payload(prospect):
    assigned = db.session.get(User, prospect.assigned_to_user_id)
    leader = User.query.filter_by(tenant_id=prospect.tenant_id, role="leader").first()
    return {
        "id": prospect.id,
        "tenant_id": prospect.tenant_id,
        "equipo": leader.username if leader else f"Equipo {prospect.tenant_id}",
        "nombre": prospect.nombre,
        "numero": prospect.numero,
        "numero_encuesta": prospect.numero_encuesta,
        "estado": prospect.estado,
        "forma_obtencion": prospect.forma_obtencion,
        "asignado_a": _user_payload(assigned),
        "total_vendido": float(prospect.venta_monto_sin_iva or 0),
        "created_at": _iso(prospect.created_at),
        "updated_at": _iso(prospect.updated_at),
    }


@admin_bp.get("/prospects")
@platform_admin_required
def prospects():
    query = Prospect.query
    tenant_id = request.args.get("tenant_id", type=int)
    if tenant_id:
        query = query.filter(Prospect.tenant_id == tenant_id)
    search = (request.args.get("q") or "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(Prospect.nombre.ilike(like), Prospect.numero.ilike(like), Prospect.numero_encuesta.ilike(like)))
    rows = query.order_by(Prospect.created_at.desc()).limit(2000).all()
    return {"prospects": [_prospect_payload(row) for row in rows]}, 200
