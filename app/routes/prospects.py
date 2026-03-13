from datetime import datetime,timedelta
from dateutil.relativedelta import relativedelta
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from sqlalchemy import func
from ..extensions import db
from ..models import Prospect, ProspectHistory, Appointment, CallReminder, User
import calendar
from sqlalchemy.orm import aliased
from ..utils.visibility import get_visible_user_id

prospects_bp = Blueprint("prospects", __name__)
def _cancelar_todo_agendado_de_prospecto(
    tenant_id: int,
    prospect_id: int,
    motivo: str,
):
    now = datetime.utcnow()

    # Cancelar TODAS las llamadas pendientes (y si quieres, también reagendadas)
    calls = (
        CallReminder.query
        .filter_by(tenant_id=tenant_id, prospect_id=prospect_id)
        .filter(CallReminder.estado.in_(["pendiente"]))  # agrega "reagendada" si aplica
        # opcional: solo futuras
        .filter(CallReminder.fecha_hora >= now)
        .all()
    )

    for c in calls:
        c.estado = "cancelada"
        # opcional: dejar rastro en observaciones
        obs = (c.observaciones or "").strip()
        extra = f"Cancelada automáticamente: {motivo}"
        c.observaciones = (obs + ("\n" if obs else "") + extra) if extra else obs

    # Cancelar TODAS las citas programadas (futuras)
    appts = (
        Appointment.query
        .filter_by(tenant_id=tenant_id, prospect_id=prospect_id)
        .filter(Appointment.estado == "programada")
        .filter(Appointment.fecha_hora >= now)
        .all()
    )

    for a in appts:
        a.estado = "cancelada"
        obs = (a.observaciones or "").strip()
        extra = f"Cancelada automáticamente: {motivo}"
        a.observaciones = (obs + ("\n" if obs else "") + extra) if extra else obs

def _fmt_dt(dt: datetime | None) -> str | None:
    if not dt:
        return None
    return dt.strftime("%Y-%m-%d %H:%M")

def _set_prospect_estado(prospect: Prospect, nuevo_estado: str | None):
    if not nuevo_estado:
        return None, None

    de_estado = prospect.estado
    prospect.estado = nuevo_estado
    return de_estado, nuevo_estado

def _get_effective_user_id(claims, actor_user_id: int) -> int:
    role = claims.get("role")
    tenant_id = claims.get("tenant_id")

    acting_as = request.headers.get("X-Acting-As-User")
    if not acting_as:
        return actor_user_id

    if role != "leader":
        return actor_user_id

    try:
        acting_as_id = int(acting_as)
    except Exception:
        return actor_user_id

    u = User.query.filter_by(id=acting_as_id, tenant_id=tenant_id).first()
    if not u:
        return actor_user_id
    return acting_as_id

def _ensure_monthly_followups(
    tenant_id: int,
    prospect_id: int,
    user_id: int,
    months: int = 12,
    hour: int = 10,
    minute: int = 0,
    day_of_month: int = 1,
):
    """
    Crea recordatorios mensuales (CallReminder) en estado 'pendiente'.
    - Mantiene el día del mes (day_of_month) y si no existe (ej 31 en feb),
      lo baja al último día disponible.
    - Evita duplicar si ya hay una llamada pendiente en ese mes.
    """
    now = datetime.utcnow()

    # empezamos desde el siguiente mes (día 1), luego iteramos mes a mes
    base = datetime(now.year, now.month, 1, hour, minute) + relativedelta(months=1)

    for i in range(months):
        dt_month = base + relativedelta(months=i)
        y, m = dt_month.year, dt_month.month

        last_day = calendar.monthrange(y, m)[1]
        safe_day = min(int(day_of_month or 1), last_day)

        target = datetime(y, m, safe_day, hour, minute)

        month_start = datetime(y, m, 1)
        month_end = month_start + relativedelta(months=1)

        exists = (
            CallReminder.query
            .filter_by(tenant_id=tenant_id, prospect_id=prospect_id)
            .filter(CallReminder.estado == "pendiente")
            .filter(CallReminder.fecha_hora >= month_start, CallReminder.fecha_hora < month_end)
            .first()
        )
        if exists:
            continue

        db.session.add(
            CallReminder(
                tenant_id=tenant_id,
                prospect_id=prospect_id,
                created_by_user_id=user_id,
                fecha_hora=target,
                observaciones="Seguimiento mensual (mantenimiento / nuevas citas)",
                estado="pendiente",
            )
        )
        
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
    h = ProspectHistory(
        tenant_id=tenant_id,
        prospect_id=prospect_id,
        actor_user_id=actor_user_id,
        effective_user_id=effective_user_id,
        accion=accion,
        de_estado=de_estado,
        a_estado=a_estado,
        detalle=detalle,
    )
    db.session.add(h)


def _prospect_to_dict(p: Prospect):
    return {
        "id": p.id,
        "nombre": p.nombre,
        "numero": p.numero,
        "observaciones": p.observaciones,
        "estado": p.estado,
        "assigned_to_user_id": p.assigned_to_user_id,
        "recomendado_por_id": p.recomendado_por_id,
        "recomendado_por_nombre": p.recomendado_por.nombre if p.recomendado_por else None,
        "forma_obtencion_tipo": p.forma_obtencion_tipo,
        "forma_obtencion": p.forma_obtencion,
        "seguimiento_pausado": bool(getattr(p, "seguimiento_pausado", False)),
        "seguimiento_pausado_at": p.seguimiento_pausado_at.isoformat() + "Z" if getattr(p, "seguimiento_pausado_at", None) else None,
        "created_at": p.created_at.isoformat(),
        "venta_monto_sin_iva": float(p.venta_monto_sin_iva) if p.venta_monto_sin_iva is not None else None,
        "venta_fecha": p.venta_fecha.isoformat() + "Z" if p.venta_fecha else None,
        "rechazo_motivo": p.rechazo_motivo,
        "rechazo_at": p.rechazo_at.isoformat() + "Z" if p.rechazo_at else None,
        "rechazo_count": int(p.rechazo_count or 0),
    }


@prospects_bp.post("/")
@jwt_required()
def crear_prospecto():
    actor_user_id = int(get_jwt_identity())
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    role = claims.get("role")

    effective_user_id = _get_effective_user_id(claims, actor_user_id)

    data = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    numero = (data.get("numero") or "").strip()
    observaciones = (data.get("observaciones") or "").strip() or None
    recomendado_por_id = data.get("recomendado_por_id")
    assigned_to_user_id = data.get("assigned_to_user_id")

    forma_obtencion_tipo = (data.get("forma_obtencion_tipo") or "").strip()
    forma_obtencion = (data.get("forma_obtencion") or "").strip()

    if not nombre or not numero:
        return {"message": "Nombre y número son obligatorios"}, 400

    if forma_obtencion_tipo not in {"encuesta", "cita_en_frio", "otro"}:
        return {"message": "forma_obtencion_tipo inválido"}, 400

    if forma_obtencion_tipo == "encuesta":
        forma_obtencion = "Encuesta"
    elif forma_obtencion_tipo == "cita_en_frio":
        forma_obtencion = "Cita en frío"
    elif forma_obtencion_tipo == "otro":
        if not forma_obtencion:
            return {"message": "Debes especificar la forma de obtención cuando eliges 'otro'"}, 400

    if role == "collaborator":
        assigned_to_user_id = effective_user_id
    else:
        if not assigned_to_user_id:
            assigned_to_user_id = effective_user_id

    try:
        assigned_to_user_id = int(assigned_to_user_id)
    except Exception:
        return {"message": "assigned_to_user_id inválido"}, 400

    assigned_user = User.query.filter_by(
        id=assigned_to_user_id,
        tenant_id=tenant_id
    ).first()
    if not assigned_user:
        return {"message": "assigned_to_user_id no pertenece a tu equipo"}, 400

    recomendado_prospect = None
    if recomendado_por_id:
        recomendado_prospect = Prospect.query.filter_by(
            id=recomendado_por_id,
            tenant_id=tenant_id
        ).first()
        if not recomendado_prospect:
            return {"message": "El prospecto recomendado no existe"}, 400

    prospect = Prospect(
        tenant_id=tenant_id,
        created_by_user_id=effective_user_id,
        assigned_to_user_id=assigned_to_user_id,
        nombre=nombre,
        numero=numero,
        observaciones=observaciones,
        recomendado_por=recomendado_prospect,
        forma_obtencion_tipo=forma_obtencion_tipo,
        forma_obtencion=forma_obtencion,
        estado="pendiente",
    )
    db.session.add(prospect)
    db.session.flush()

    detalle_historial = observaciones or f"Forma de obtención: {forma_obtencion}"

    _log_history(
        tenant_id=tenant_id,
        prospect_id=prospect.id,
        actor_user_id=actor_user_id,
        effective_user_id=effective_user_id,
        accion="crear_prospecto",
        de_estado=None,
        a_estado="pendiente",
        detalle=detalle_historial,
    )

    db.session.commit()
    return {"prospecto": _prospect_to_dict(prospect)}, 201


@prospects_bp.get("/")
@jwt_required()
def listar_prospectos():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))

    estado = request.args.get("estado")
    q = (request.args.get("q") or "").strip()

    query = Prospect.query.filter_by(
        tenant_id=tenant_id,
        assigned_to_user_id=visible_user_id,
    )

    if estado:
        query = query.filter_by(estado=estado)

    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Prospect.nombre.ilike(like), Prospect.numero.ilike(like)))

    query = query.order_by(Prospect.created_at.desc())
    return {"prospectos": [_prospect_to_dict(p) for p in query.all()]}, 200


@prospects_bp.get("/recomendadores")
@jwt_required()
def buscar_recomendadores():
    """Para autocompletar el 'Recomendado por' solo con prospectos ya existentes."""
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))

    q = (request.args.get("q") or "").strip()
    if not q:
        return {"prospectos": []}, 200

    like = f"%{q}%"
    results = (
        Prospect.query.filter_by(
            tenant_id=tenant_id,
            assigned_to_user_id=visible_user_id,
        )
        .filter(Prospect.nombre.ilike(like))
        .order_by(Prospect.nombre.asc())
        .limit(10)
        .all()
    )

    return {
        "prospectos": [
            {"id": p.id, "nombre": p.nombre, "numero": p.numero} for p in results
        ]
    }, 200


@prospects_bp.post("/<int:prospect_id>/acciones")
@jwt_required()
def accion_prospecto(prospect_id: int):
    actor_user_id = int(get_jwt_identity())
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    effective_user_id = _get_effective_user_id(claims, actor_user_id)

    data = request.get_json() or {}
    accion = data.get("accion")
    prospect = Prospect.query.filter_by(
        id=prospect_id,
        tenant_id=tenant_id,
        assigned_to_user_id=effective_user_id,
    ).first()
    if not prospect:
        return {"message": "Prospecto no encontrado"}, 404
    
    locked_states = {"anexado"}
    if prospect.estado in locked_states and accion in {"agendar_cita", "programar_llamada"}:
        return {"message": "No puedes agendar sobre un prospecto anexado."}, 409
    if accion not in {
        "sin_respuesta",
        "rechazado",
        "agendar_cita",
        "programar_llamada",
        "observaciones",
        "recuperar",
        "anexar",
        "vendido",
        "iniciar_seguimiento", 
        "pausar_seguimiento",
    }:
        return {"message": "Acción no soportada"}, 400

    de_estado = prospect.estado
    detalle = None

    if accion == "sin_respuesta":
        prospect.estado = "sin_respuesta"
        detalle = data.get("motivo") or "Marcado como sin respuesta"

    elif accion == "rechazado":
        prospect.estado = "rechazado"
        detalle = (data.get("motivo") or "").strip() or "Marcado como rechazado"

        prospect.rechazo_motivo = detalle
        prospect.rechazo_at = datetime.utcnow()
        prospect.rechazo_count = (prospect.rechazo_count or 0) + 1

        _cancelar_todo_agendado_de_prospecto(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            motivo=f"Prospecto rechazado. {detalle}",
        )

    elif accion == "agendar_cita":
        fecha = data.get("fecha")
        hora = data.get("hora")
        ubicacion = (data.get("ubicacion") or "").strip()
        obs = (data.get("observaciones") or "").strip() or None

        if not fecha or not hora or not ubicacion:
            return {"message": "Fecha, hora y ubicación son obligatorias"}, 400

        fecha_hora = datetime.fromisoformat(f"{fecha}T{hora}")

        cita_prev = (
            Appointment.query
            .filter_by(tenant_id=tenant_id, prospect_id=prospect.id)
            .filter(Appointment.estado == "programada")
            .order_by(Appointment.fecha_hora.desc())
            .first()
        )

        if cita_prev:
            cita_prev.estado = "reagendada"
            cita_prev.resolved_at = datetime.utcnow()
            cita_prev.estado_detalle = f"Reagendada para {_fmt_dt(fecha_hora)}"

        cita = Appointment(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            created_by_user_id=effective_user_id,
            fecha_hora=fecha_hora,
            ubicacion=ubicacion,
            observaciones=obs,
            estado="programada",
            estado_detalle=None,
        )
        db.session.add(cita)

        prospect.estado = "con_cita"

        if cita_prev:
            detalle = f"Cita reagendada para {_fmt_dt(fecha_hora)} en {ubicacion}"
        else:
            detalle = f"Cita programada para {_fmt_dt(fecha_hora)} en {ubicacion}"


    elif accion == "programar_llamada":
        fecha = data.get("fecha")
        hora = data.get("hora")
        obs = (data.get("observaciones") or "").strip() or None

        if not fecha or not hora:
            return {"message": 'Fecha y hora son obligatorias para "programar_llamada"'}, 400

        fecha_hora = datetime.fromisoformat(f"{fecha}T{hora}")
        llamada = CallReminder(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            created_by_user_id=effective_user_id,
            fecha_hora=fecha_hora,
            observaciones=obs,
            estado="pendiente",
            estado_detalle=None,
        )
        db.session.add(llamada)

        detalle = f"Llamada programada para {_fmt_dt(fecha_hora)}"


    elif accion == "observaciones":
        obs = (data.get("observaciones") or "").strip()
        if not obs:
            return {"message": "Las observaciones no pueden estar vacías"}, 400

        if prospect.observaciones:
            prospect.observaciones = prospect.observaciones + "\n" + obs
        else:
            prospect.observaciones = obs

        detalle = f"Observaciones añadidas: {obs}"
    elif accion == "recuperar":
        prospect.estado = "pendiente"
        detalle = data.get("motivo") or "Prospecto recuperado"
        prospect.rechazo_motivo = None
        prospect.rechazo_at = None

    elif accion == "anexar":
        prospect.estado = "anexado"
        detalle = data.get("motivo") or "Prospecto anexado"

        _cancelar_todo_agendado_de_prospecto(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            motivo=f"Prospecto anexado. {detalle}",
        )
    elif accion == "vendido":
        monto = data.get("monto_sin_iva")
        if monto is None:
            return {"message": "monto_sin_iva es obligatorio"}, 400

        try:
            monto = float(monto)
        except Exception:
            return {"message": "monto_sin_iva inválido"}, 400

        if monto <= 0:
            return {"message": "monto_sin_iva debe ser mayor a 0"}, 400

        prospect.venta_monto_sin_iva = monto
        prospect.venta_fecha = datetime.utcnow()

        prospect.estado = "seguimiento"

        cita_actual = (
            Appointment.query
            .filter_by(tenant_id=tenant_id, prospect_id=prospect.id)
            .filter(Appointment.estado == "programada")
            .order_by(Appointment.fecha_hora.desc())
            .first()
        )
        if cita_actual:
            cita_actual.estado = "vendida"

        detalle = f"Vendido (sin IVA): {monto}"

    elif accion == "iniciar_seguimiento":
        if prospect.venta_monto_sin_iva is None:
            return {"message": "No puedes iniciar seguimiento si no hay venta registrada."}, 409

        prospect.estado = "seguimiento"
        prospect.seguimiento_pausado = False
        prospect.seguimiento_pausado_at = None

        sale_date = prospect.venta_fecha or datetime.utcnow()
        _ensure_monthly_followups(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            user_id=effective_user_id,
            months=12,
            hour=10,
            minute=0,
            day_of_month=sale_date.day,
        )

        detalle = "Seguimiento iniciado. Recordatorios mensuales creados."

    elif accion == "pausar_seguimiento":
        if prospect.estado != "seguimiento":
            return {"message": "Solo puedes pausar seguimiento en prospectos en seguimiento."}, 409

        prospect.seguimiento_pausado = True
        prospect.seguimiento_pausado_at = datetime.utcnow()

        now = datetime.utcnow()
        calls = (
            CallReminder.query
            .filter_by(tenant_id=tenant_id, prospect_id=prospect.id)
            .filter(CallReminder.estado == "pendiente")
            .filter(CallReminder.fecha_hora >= now)
            .filter(CallReminder.observaciones == "Seguimiento mensual (mantenimiento / nuevas citas)")
            .all()
        )

        for c in calls:
            c.estado = "cancelada"
            c.estado_detalle = "Seguimiento pausado"
            c.resolved_at = datetime.utcnow()

        detalle = "Seguimiento pausado. Recordatorios pendientes cancelados."

    _log_history(
        tenant_id=tenant_id,
        prospect_id=prospect.id,
        actor_user_id=actor_user_id,
        effective_user_id=effective_user_id,
        accion=accion,
        de_estado=de_estado,
        a_estado=prospect.estado,
        detalle=detalle,
    )

    db.session.commit()

    return {"prospecto": _prospect_to_dict(prospect)}, 200


@prospects_bp.get("/<int:prospect_id>/historial")
@jwt_required()
def ver_historial(prospect_id: int):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))

    prospect = Prospect.query.filter_by(
        id=prospect_id,
        tenant_id=tenant_id,
        assigned_to_user_id=visible_user_id,
    ).first()
    if not prospect:
        return {"message": "Prospecto no encontrado"}, 404

    Actor = aliased(User)
    Effective = aliased(User)

    rows = (
        db.session.query(ProspectHistory, Actor, Effective)
        .filter(ProspectHistory.tenant_id == tenant_id, ProspectHistory.prospect_id == prospect_id)
        .outerjoin(Actor, Actor.id == ProspectHistory.actor_user_id)
        .outerjoin(Effective, Effective.id == ProspectHistory.effective_user_id)
        .order_by(ProspectHistory.created_at.desc())
        .all()
    )

    historial = []
    for (h, actor_u, eff_u) in rows:
        actor_payload = {"id": h.actor_user_id, "email": actor_u.email if actor_u else None}
        effective_payload = {"id": h.effective_user_id, "email": eff_u.email if eff_u else None}

        historial.append({
            "id": h.id,
            "accion": h.accion,
            "created_at": h.created_at.isoformat() + "Z",
            "de_estado": h.de_estado,
            "a_estado": h.a_estado,
            "detalle": h.detalle,

            "user": effective_payload,
            "actor": actor_payload,
            "effective": effective_payload,
        })

    return {
        "prospect": {
            "id": prospect.id,
            "nombre": prospect.nombre,
            "numero": prospect.numero,
            "observaciones": prospect.observaciones,
            "estado": prospect.estado,
            "created_at": prospect.created_at.isoformat() + "Z",
        },
        "historial": historial,
    }, 200

@prospects_bp.get("/seguimiento")
@jwt_required()
def listar_seguimiento():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))

    q = (request.args.get("q") or "").strip().lower()
    limit = int(request.args.get("limit") or 200)

    # subquery: próxima llamada pendiente por prospecto
    # subquery: próxima llamada pendiente general
    sub = (
        db.session.query(
            CallReminder.prospect_id.label("prospect_id"),
            func.min(CallReminder.fecha_hora).label("proxima_llamada"),
        )
        .filter(CallReminder.tenant_id == tenant_id)
        .filter(CallReminder.estado == "pendiente")
        .group_by(CallReminder.prospect_id)
        .subquery()
    )

    # subquery: próxima llamada de seguimiento mensual
    sub_followup = (
        db.session.query(
            CallReminder.prospect_id.label("prospect_id"),
            func.min(CallReminder.fecha_hora).label("proxima_llamada_seguimiento"),
        )
        .filter(CallReminder.tenant_id == tenant_id)
        .filter(CallReminder.estado == "pendiente")
        .filter(CallReminder.observaciones == "Seguimiento mensual (mantenimiento / nuevas citas)")
        .group_by(CallReminder.prospect_id)
        .subquery()
    )

    query = (
        db.session.query(
            Prospect,
            sub.c.proxima_llamada,
            sub_followup.c.proxima_llamada_seguimiento,
        )
        .outerjoin(sub, sub.c.prospect_id == Prospect.id)
        .outerjoin(sub_followup, sub_followup.c.prospect_id == Prospect.id)
        .filter(Prospect.tenant_id == tenant_id)
        .filter(Prospect.assigned_to_user_id == visible_user_id)
        .filter(Prospect.estado == "seguimiento")
        .order_by(Prospect.updated_at.desc())
    )

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Prospect.nombre.ilike(like),
                Prospect.numero.ilike(like),
            )
        )

    rows = query.limit(limit).all()

    return {
        "seguimiento": [
            {
                **_prospect_to_dict(p),
                "proxima_llamada": dt.isoformat() + "Z" if dt else None,
                "proxima_llamada_seguimiento": dt_follow.isoformat() + "Z" if dt_follow else None,
                "seguimiento_activo": bool(dt_follow) and not bool(getattr(p, "seguimiento_pausado", False)),
            }
            for (p, dt, dt_follow) in rows
        ]
    }, 200

@prospects_bp.get("/stats")
@jwt_required()
def prospect_stats():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))

    rows = (
        db.session.query(Prospect.estado, func.count(Prospect.id))
        .filter(Prospect.tenant_id == tenant_id)
        .filter(Prospect.assigned_to_user_id == visible_user_id)
        .group_by(Prospect.estado)
        .all()
    )

    by_estado = {estado: int(c) for (estado, c) in rows}
    total = sum(by_estado.values())

    # helpers: devuelve 0 si no existe el estado
    def g(key: str) -> int:
        return int(by_estado.get(key, 0))

    return {
        "total": total,
        "pendientes": g("pendiente"),
        "sin_respuesta": g("sin_respuesta"),
        "by_estado": by_estado, 
    }, 200

@prospects_bp.get("/<int:prospect_id>/amigos")
@jwt_required()
def prospect_amigos(prospect_id: int):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))

    p = Prospect.query.filter_by(
        id=prospect_id,
        tenant_id=tenant_id,
        assigned_to_user_id=visible_user_id,
    ).first()
    if not p:
        return {"message": "Prospecto no encontrado"}, 404

    recomendado_por = None
    if p.recomendado_por_id:
        rp = Prospect.query.filter_by(id=p.recomendado_por_id, tenant_id=tenant_id).first()
        if rp:
            recomendado_por = _prospect_to_dict(rp)

    recomendados = (
        Prospect.query
        .filter_by(
            tenant_id=tenant_id,
            recomendado_por_id=p.id,
            assigned_to_user_id=visible_user_id,
        )
        .order_by(Prospect.created_at.desc())
        .all()
    )

    return {
        "recomendado_por": recomendado_por,
        "recomendados": [_prospect_to_dict(x) for x in recomendados],
    }, 200

@prospects_bp.get("/search")
@jwt_required()
def buscar_prospectos_global():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    visible_user_id = get_visible_user_id(claims, int(get_jwt_identity()))

    q = (request.args.get("q") or "").strip()
    estado = (request.args.get("estado") or "todos").strip().lower()
    limit = int(request.args.get("limit") or 100)

    if limit <= 0:
        limit = 100
    if limit > 300:
        limit = 300

    def apply_common_filters(query):
        query = query.filter(
            Prospect.tenant_id == tenant_id,
            Prospect.assigned_to_user_id == visible_user_id,
            Prospect.estado != "anexado",
        )

        if estado and estado != "todos":
            query = query.filter(Prospect.estado == estado)

        if q:
            like = f"%{q}%"
            query = query.filter(
                db.or_(
                    Prospect.nombre.ilike(like),
                    Prospect.numero.ilike(like),
                    Prospect.observaciones.ilike(like),
                    Prospect.forma_obtencion.ilike(like),
                )
            )

        return query

    query = apply_common_filters(Prospect.query)
    prospectos = (
        query.order_by(Prospect.updated_at.desc(), Prospect.created_at.desc())
        .limit(limit)
        .all()
    )

    resumen_query = apply_common_filters(
        db.session.query(
            Prospect.estado.label("estado"),
            func.count(Prospect.id).label("cantidad"),
        )
    )

    resumen_rows = (
        resumen_query
        .group_by(Prospect.estado)
        .order_by(Prospect.estado.asc())
        .all()
    )

    return {
        "prospectos": [_prospect_to_dict(p) for p in prospectos],
        "resumen_estados": [
            {
                "estado": r.estado,
                "cantidad": int(r.cantidad),
            }
            for r in resumen_rows
        ],
        "total": sum(int(r.cantidad) for r in resumen_rows),
    }, 200