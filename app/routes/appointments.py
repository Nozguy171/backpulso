# app/routes/appointments.py
from datetime import datetime, date, timedelta
from flask import Blueprint, request
from sqlalchemy import and_
from sqlalchemy import func
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from ..extensions import db
from ..models import Appointment, Prospect, ProspectHistory, User
from ..utils.visibility import get_visible_user_id

appointments_bp = Blueprint("appointments", __name__)
APPOINTMENT_ESTADO_LABELS = {
    "programada": "Pendiente",
    "realizada": "Realizada",
    "reagendada": "Reagendada",
    "cancelada": "Cancelada",
    "vendida": "Vendida",
    "rechazada": "Rechazada",
    "anexada": "Anexada",
}

def _humanize_key(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("_", " ").strip().capitalize()

def _label_from_map(value: str | None, mapping: dict[str, str]) -> str:
    if not value:
        return "—"
    return mapping.get(value, _humanize_key(value))

def _log_history(
    tenant_id: int,
    prospect_id: int,
    actor_user_id: int,
    effective_user_id: int,
    accion: str,
    de_estado: str | None = None,
    a_estado: str | None = None,
    detalle: str | None = None,
):
    db.session.add(ProspectHistory(
        tenant_id=tenant_id,
        prospect_id=prospect_id,
        actor_user_id=actor_user_id,
        effective_user_id=effective_user_id,
        accion=accion,
        de_estado=de_estado,
        a_estado=a_estado,
        detalle=detalle,
    ))

def _appt_to_dict(a: Appointment):
    return {
        "id": a.id,
        "fecha_hora": a.fecha_hora.isoformat(),
        "ubicacion": a.ubicacion,
        "ubicacion_lat": a.ubicacion_lat,
        "ubicacion_lng": a.ubicacion_lng,
        "observaciones": a.observaciones,
        "estado": a.estado,
        "estado_label": _label_from_map(a.estado, APPOINTMENT_ESTADO_LABELS),
        "estado_detalle": a.estado_detalle,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "created_at": a.created_at.isoformat() if getattr(a, "created_at", None) else None,
        "prospect": {
            "id": a.prospect.id,
            "nombre": a.prospect.nombre,
            "numero": a.prospect.numero,
            "lada": a.prospect.lada_display,
            "numero_formateado": a.prospect.numero_formateado,
            "numero_encuesta": a.prospect.numero_encuesta,
            "estado": a.prospect.estado,
            "forma_obtencion_tipo": a.prospect.forma_obtencion_tipo,
            "forma_obtencion": a.prospect.forma_obtencion,
            "ultima_ubicacion_cita": a.prospect.ultima_ubicacion_cita,
            "ultima_ubicacion_cita_lat": a.prospect.ultima_ubicacion_cita_lat,
            "ultima_ubicacion_cita_lng": a.prospect.ultima_ubicacion_cita_lng,
            "created_at": a.prospect.created_at.isoformat() if a.prospect.created_at else None,
            "seguimiento_pausado": bool(getattr(a.prospect, "seguimiento_pausado", False)),
            "seguimiento_fecha_base": a.prospect.seguimiento_fecha_base.isoformat() if getattr(a.prospect, "seguimiento_fecha_base", None) else None,
            "venta_monto_sin_iva": float(a.prospect.venta_monto_sin_iva) if a.prospect.venta_monto_sin_iva is not None else None,
        } if a.prospect else None,
        "user": {
            "id": a.created_by_user_id,
            "email": a.created_by_user.email,
        } if getattr(a, "created_by_user", None) else {"id": a.created_by_user_id},
    }
@appointments_bp.get("/")
@jwt_required()
def listar_citas():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))
    # filtros
    day = request.args.get("day")        # "2026-01-13"
    from_ = request.args.get("from")     # ISO o "YYYY-MM-DD"
    to_ = request.args.get("to")         # ISO o "YYYY-MM-DD"
    limit = int(request.args.get("limit") or 200)
    estado = request.args.get("estado")  # opcional: programada/cancelada/etc

    q = (
        Appointment.query
        .filter(Appointment.tenant_id == tenant_id)
        .join(Prospect, Prospect.id == Appointment.prospect_id)
        .outerjoin(User, User.id == Appointment.created_by_user_id)
        .order_by(Appointment.fecha_hora.asc())
    )
    prospect_id = request.args.get("prospect_id")
    q = q.filter(Prospect.assigned_to_user_id == visible_user_id)
    if estado:
        q = q.filter(Appointment.estado == estado)
    if prospect_id:
        q = q.filter(Appointment.prospect_id == int(prospect_id))
    # day tiene prioridad
    if day:
        d = datetime.fromisoformat(day).date()
        start = datetime.combine(d, datetime.min.time())
        end = start + timedelta(days=1)
        q = q.filter(and_(Appointment.fecha_hora >= start, Appointment.fecha_hora < end))
    else:
        if from_:
            # acepta YYYY-MM-DD o ISO
            if len(from_) == 10:
                d = datetime.fromisoformat(from_).date()
                from_dt = datetime.combine(d, datetime.min.time())
            else:
                from_dt = datetime.fromisoformat(from_.replace("Z", ""))
            q = q.filter(Appointment.fecha_hora >= from_dt)

        if to_:
            if len(to_) == 10:
                d = datetime.fromisoformat(to_).date()
                to_dt = datetime.combine(d, datetime.min.time()) + timedelta(days=1)
            else:
                to_dt = datetime.fromisoformat(to_.replace("Z", ""))
            q = q.filter(Appointment.fecha_hora < to_dt)

    rows = q.limit(limit).all()
    return {"citas": [_appt_to_dict(a) for a in rows]}, 200


@appointments_bp.get("/days")
@jwt_required()
def dias_con_citas():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))

    from_ = request.args.get("from")
    to_ = request.args.get("to")
    estado = request.args.get("estado")

    q = (
        db.session.query(
            func.date(Appointment.fecha_hora).label("day"),
            func.count(Appointment.id).label("count"),
        )
        .join(Prospect, Prospect.id == Appointment.prospect_id)
        .filter(Appointment.tenant_id == tenant_id)
        .filter(Prospect.assigned_to_user_id == visible_user_id)
    )

    if estado:
        q = q.filter(Appointment.estado == estado)

    q = q.group_by(func.date(Appointment.fecha_hora)).order_by(func.date(Appointment.fecha_hora).asc())

    if from_:
        d = datetime.fromisoformat(from_).date()
        q = q.filter(Appointment.fecha_hora >= datetime.combine(d, datetime.min.time()))
    if to_:
        d = datetime.fromisoformat(to_).date()
        q = q.filter(Appointment.fecha_hora < datetime.combine(d, datetime.min.time()) + timedelta(days=1))

    rows = q.all()

    return {
        "days": [{"day": str(r.day), "count": int(r.count)} for r in rows]
    }, 200


@appointments_bp.post("/<int:appointment_id>/marcar-realizada")
@jwt_required()
def marcar_cita_realizada(appointment_id: int):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    actor_user_id = int(get_jwt_identity())
    visible_user_id = get_visible_user_id(claims, actor_user_id)

    data = request.get_json() or {}
    obs = (data.get("observaciones") or "").strip() or None

    cita = (
        Appointment.query
        .filter(Appointment.id == appointment_id, Appointment.tenant_id == tenant_id)
        .join(Prospect, Prospect.id == Appointment.prospect_id)
        .filter(Prospect.assigned_to_user_id == visible_user_id)
        .first()
    )
    if not cita:
        return {"message": "Cita no encontrada"}, 404

    old_estado = cita.estado
    old_prospect_estado = cita.prospect.estado if cita.prospect else None
    cita.estado = "realizada"
    cita.estado_detalle = obs or "Cita realizada"
    cita.resolved_at = datetime.now()
    if cita.prospect and cita.prospect.venta_monto_sin_iva is None and cita.prospect.estado not in {"anexado", "rechazado"}:
        cita.prospect.estado = "pendiente"
    _log_history(
        tenant_id=tenant_id,
        prospect_id=cita.prospect_id,
        actor_user_id=actor_user_id,
        effective_user_id=visible_user_id,
        accion="observaciones",
        de_estado=old_prospect_estado,
        a_estado=cita.prospect.estado if cita.prospect else None,
        detalle=f"[CITA] {cita.estado_detalle}",
    )

    db.session.commit()
    return {"ok": True, "cita": _appt_to_dict(cita)}, 200
