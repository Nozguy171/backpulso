from datetime import datetime,timedelta
from dateutil.relativedelta import relativedelta
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from sqlalchemy import func
from ..extensions import db
from ..models import Prospect, ProspectHistory, Appointment, CallReminder, User, ProspectSale
import calendar
from sqlalchemy.orm import aliased
from ..utils.visibility import get_visible_user_id

prospects_bp = Blueprint("prospects", __name__)
ACCION_LABELS = {
    "crear_prospecto": "Prospecto creado",
    "sin_respuesta": "Sin respuesta",
    "rechazado": "Prospecto rechazado",
    "agendar_cita": "Cita agendada",
    "programar_llamada": "Llamada programada",
    "observaciones": "Observaciones añadidas",
    "recuperar": "Prospecto recuperado",
    "anexar": "Prospecto anexado",
    "vendido": "Venta registrada",
    "iniciar_seguimiento": "Seguimiento iniciado",
    "pausar_seguimiento": "Seguimiento pausado",
}
FOLLOWUP_OBS = "Seguimiento mensual (mantenimiento / nuevas citas)"
PROSPECT_ESTADO_LABELS = {
    "pendiente": "Pendiente",
    "sin_respuesta": "Sin respuesta",
    "con_cita": "Con cita",
    "rechazado": "Rechazado",
    "seguimiento": "Seguimiento",
    "anexado": "Anexado",
}

APPOINTMENT_ESTADO_LABELS = {
    "programada": "Pendiente",
    "reagendada": "Reagendada",
    "cancelada": "Cancelada",
    "vendida": "Vendida",
    "rechazada": "Rechazada",
    "anexada": "Anexada",
}

CALL_ESTADO_LABELS = {
    "pendiente": "Pendiente",
    "hecha": "Hecha",
    "cancelada": "Cancelada",
    "reagendada": "Reagendada",
    "con_cita": "Cita agendada",
    "vendida": "Vendida",
    "rechazada": "Rechazada",
    "sin_respuesta": "Sin respuesta",
    "anexada": "Anexada",
}

VENTA_TIPO_LABELS = {
    "contado": "Contado",
    "credito": "Crédito",
}



def _humanize_key(value: str | None) -> str:
    if not value:
        return "—"
    return value.replace("_", " ").strip().capitalize()


def _label_from_map(value: str | None, mapping: dict[str, str]) -> str:
    if not value:
        return "—"
    return mapping.get(value, _humanize_key(value))

def _cancelar_todo_agendado_de_prospecto(
    tenant_id: int,
    prospect_id: int,
    motivo: str,
    appointment_estado: str = "cancelada",
    call_estado: str = "cancelada",
    exclude_appointment_id: int | None = None,
    exclude_call_id: int | None = None,
    include_followup_calls: bool = True,
):
    now = datetime.now()

    calls_query = (
        CallReminder.query
        .filter_by(tenant_id=tenant_id, prospect_id=prospect_id)
        .filter(CallReminder.estado.in_(["pendiente", "con_cita"]))
    )

    if exclude_call_id is not None:
        calls_query = calls_query.filter(CallReminder.id != exclude_call_id)

    calls = calls_query.all()
    if not include_followup_calls:
        calls_query = calls_query.filter(
            db.or_(
                CallReminder.observaciones.is_(None),
                CallReminder.observaciones != FOLLOWUP_OBS,
            )
        )
    for c in calls:
        c.estado = call_estado
        c.estado_detalle = motivo
        c.resolved_at = now

        obs = (c.observaciones or "").strip()
        extra = f"Resuelta automáticamente: {motivo}"
        c.observaciones = (obs + ("\n" if obs else "") + extra) if extra else obs

    appts_query = (
        Appointment.query
        .filter_by(tenant_id=tenant_id, prospect_id=prospect_id)
        .filter(Appointment.estado == "programada")
    )

    if exclude_appointment_id is not None:
        appts_query = appts_query.filter(Appointment.id != exclude_appointment_id)

    appts = appts_query.all()

    for a in appts:
        a.estado = appointment_estado
        a.estado_detalle = motivo
        a.resolved_at = now

        obs = (a.observaciones or "").strip()
        extra = f"Resuelta automáticamente: {motivo}"
        a.observaciones = (obs + ("\n" if obs else "") + extra) if extra else obs


def _cancelar_llamadas_pendientes_de_prospecto(
    tenant_id: int,
    prospect_id: int,
    motivo: str,
    exclude_call_id: int | None = None,
    include_followup_calls: bool = True,
):
    now = datetime.now()

    calls_query = (
        CallReminder.query
        .filter_by(tenant_id=tenant_id, prospect_id=prospect_id)
        .filter(CallReminder.estado == "pendiente")
    )

    if exclude_call_id is not None:
        calls_query = calls_query.filter(CallReminder.id != exclude_call_id)
    if not include_followup_calls:
        calls_query = calls_query.filter(
            db.or_(
                CallReminder.observaciones.is_(None),
                CallReminder.observaciones != FOLLOWUP_OBS,
            )
        )
    calls = calls_query.all()

    for c in calls:
        c.estado = "cancelada"
        c.estado_detalle = motivo
        c.resolved_at = now

        obs = (c.observaciones or "").strip()
        extra = f"Resuelta automáticamente: {motivo}"
        c.observaciones = (obs + ("\n" if obs else "") + extra) if extra else obs

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


def _ensure_monthly_followups(
    tenant_id: int,
    prospect_id: int,
    user_id: int,
    anchor_dt: datetime,
    months: int = 12,
    start_month_offset: int = 1,
):
    """
    Genera llamadas mensuales tomando como base `anchor_dt`.
    - Si el día no existe en un mes, usa el último día disponible.
    - `start_month_offset=1`: empieza el siguiente mes (inicio normal).
    - `start_month_offset=0`: incluye el mismo mes de anchor_dt (reanudar).
    - Evita duplicar SOLO recordatorios mensuales de seguimiento.
    """
    created = 0
    offset = start_month_offset

    while created < months:
        dt_month = anchor_dt + relativedelta(months=offset)
        y, m = dt_month.year, dt_month.month

        last_day = calendar.monthrange(y, m)[1]
        safe_day = min(anchor_dt.day, last_day)

        target = datetime(y, m, safe_day, anchor_dt.hour, anchor_dt.minute)

        month_start = datetime(y, m, 1)
        month_end = month_start + relativedelta(months=1)

        exists = (
            CallReminder.query
            .filter_by(tenant_id=tenant_id, prospect_id=prospect_id)
            .filter(CallReminder.estado == "pendiente")
            .filter(CallReminder.observaciones == FOLLOWUP_OBS)
            .filter(CallReminder.fecha_hora >= month_start, CallReminder.fecha_hora < month_end)
            .first()
        )
        if not exists:
            db.session.add(
                CallReminder(
                    tenant_id=tenant_id,
                    prospect_id=prospect_id,
                    created_by_user_id=user_id,
                    fecha_hora=target,
                    observaciones=FOLLOWUP_OBS,
                    estado="pendiente",
                )
            )
            created += 1

        offset += 1
        
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
        "estado_label": _label_from_map(p.estado, PROSPECT_ESTADO_LABELS),
        "assigned_to_user_id": p.assigned_to_user_id,
        "recomendado_por_id": p.recomendado_por_id,
        "recomendado_por_nombre": p.recomendado_por.nombre if p.recomendado_por else None,
        "forma_obtencion_tipo": p.forma_obtencion_tipo,
        "forma_obtencion": p.forma_obtencion,
        "seguimiento_pausado": bool(getattr(p, "seguimiento_pausado", False)),
        "seguimiento_pausado_at": p.seguimiento_pausado_at.isoformat() if getattr(p, "seguimiento_pausado_at", None) else None,
        "seguimiento_fecha_base": p.seguimiento_fecha_base.isoformat() if getattr(p, "seguimiento_fecha_base", None) else None,
        "created_at": p.created_at.isoformat(),
        "venta_monto_sin_iva": float(p.venta_monto_sin_iva) if p.venta_monto_sin_iva is not None else None,
        "venta_fecha": p.venta_fecha.isoformat() if p.venta_fecha else None,
        "venta_tipo": p.venta_tipo,
        "venta_tipo_label": _label_from_map(p.venta_tipo, VENTA_TIPO_LABELS) if getattr(p, "venta_tipo", None) else None,
        "rechazo_motivo": p.rechazo_motivo,
        "rechazo_at": p.rechazo_at.isoformat() if p.rechazo_at else None,
        "rechazo_count": int(p.rechazo_count or 0),
    }

def _appointment_to_dict(a: Appointment):
    return {
        "id": a.id,
        "fecha_hora": a.fecha_hora.isoformat() if a.fecha_hora else None,       
        "ubicacion": a.ubicacion,
        "observaciones": a.observaciones,
        "estado": a.estado,
        "estado_label": _label_from_map(a.estado, APPOINTMENT_ESTADO_LABELS),
        "estado_detalle": a.estado_detalle,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


def _call_to_dict(c: CallReminder):
    return {
        "id": c.id,
        "fecha_hora": c.fecha_hora.isoformat() if c.fecha_hora else None,
        "observaciones": c.observaciones,
        "estado": c.estado,
        "estado_label": _label_from_map(c.estado, CALL_ESTADO_LABELS),
        "estado_detalle": c.estado_detalle,
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }

def _sale_to_dict(s: ProspectSale):
    return {
        "id": s.id,
        "tipo_venta": s.tipo_venta,
        "tipo_venta_label": _label_from_map(s.tipo_venta, VENTA_TIPO_LABELS),
        "monto_con_iva": float(s.monto_con_iva or 0),
        "iva_monto": float(s.iva_monto or 0),
        "monto_sin_iva": float(s.monto_sin_iva or 0),
        "appointment_id": s.appointment_id,
        "call_id": s.call_id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }

@prospects_bp.post("/")
@jwt_required()
def crear_prospecto():
    actor_user_id = int(get_jwt_identity())
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    role = claims.get("role")

    visible_user_id = get_visible_user_id(claims, actor_user_id)

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
        assigned_to_user_id = visible_user_id
    else:
        if not assigned_to_user_id:
            assigned_to_user_id = visible_user_id

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
        created_by_user_id=actor_user_id,
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

    detalle_historial = f"Forma de obtención: {forma_obtencion}"

    _log_history(
        tenant_id=tenant_id,
        prospect_id=prospect.id,
        actor_user_id=actor_user_id,
        effective_user_id=visible_user_id,
        accion="crear_prospecto",
        de_estado=None,
        a_estado="pendiente",
        detalle=detalle_historial,
    )

    if observaciones:
        _log_history(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            actor_user_id=actor_user_id,
            effective_user_id=visible_user_id,
            accion="observaciones",
            de_estado="pendiente",
            a_estado="pendiente",
            detalle=f"Observaciones añadidas: {observaciones}",
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
    visible_user_id = get_visible_user_id(claims, actor_user_id)

    data = request.get_json() or {}
    accion = data.get("accion")
    appointment_id = data.get("appointment_id")
    call_id = data.get("call_id")

    prospect = Prospect.query.filter_by(
        id=prospect_id,
        tenant_id=tenant_id,
        assigned_to_user_id=visible_user_id,
    ).first()
    if not prospect:
        return {"message": "Prospecto no encontrado"}, 404

    target_appointment = None
    if appointment_id is not None:
        try:
            appointment_id = int(appointment_id)
        except Exception:
            return {"message": "appointment_id inválido"}, 400

        target_appointment = Appointment.query.filter_by(
            id=appointment_id,
            tenant_id=tenant_id,
            prospect_id=prospect.id,
        ).first()

        if not target_appointment:
            return {"message": "La cita indicada no existe para este prospecto"}, 404
    target_call = None
    if call_id is not None:
        try:
            call_id = int(call_id)
        except Exception:
            return {"message": "call_id inválido"}, 400

        target_call = CallReminder.query.filter_by(
            id=call_id,
            tenant_id=tenant_id,
            prospect_id=prospect.id,
        ).first()

        if not target_call:
            return {"message": "La llamada indicada no existe para este prospecto"}, 404
    locked_states = {"anexado"}
    has_sale = prospect.venta_monto_sin_iva is not None

    if prospect.estado in locked_states and accion in {"agendar_cita", "programar_llamada"}:
        return {"message": "No puedes agendar sobre un prospecto anexado."}, 409

    if has_sale and accion in {"rechazado", "anexar", "sin_respuesta"}:
        return {"message": "No puedes rechazar, marcar sin respuesta ni anexar un prospecto que ya tiene ventas registradas."}, 409

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
        detalle = (data.get("motivo") or "").strip() or "Marcado como sin respuesta"

        if target_call and target_call.estado == "pendiente":
            target_call.estado = "sin_respuesta"
            target_call.estado_detalle = detalle
            target_call.resolved_at = datetime.now()

    elif accion == "rechazado":
        prospect.estado = "rechazado"
        detalle = (data.get("motivo") or "").strip() or "Marcado como rechazado"

        now = datetime.now()
        prospect.rechazo_motivo = detalle
        prospect.rechazo_at = now
        prospect.rechazo_count = (prospect.rechazo_count or 0) + 1

        motivo_resolucion = f"Prospecto rechazado. {detalle}"

        if target_call and target_call.estado in {"pendiente", "con_cita"}:
            target_call.estado = "rechazada"
            target_call.estado_detalle = motivo_resolucion
            target_call.resolved_at = now

        if target_appointment and target_appointment.estado == "programada":
            target_appointment.estado = "rechazada"
            target_appointment.estado_detalle = motivo_resolucion
            target_appointment.resolved_at = now

        _cancelar_todo_agendado_de_prospecto(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            motivo=motivo_resolucion,
            appointment_estado="rechazada",
            call_estado="rechazada",
        )
    elif accion == "agendar_cita":
        fecha = data.get("fecha")
        hora = data.get("hora")
        ubicacion = (data.get("ubicacion") or "").strip()
        obs = (data.get("observaciones") or "").strip() or None

        if not fecha or not hora or not ubicacion:
            return {"message": "Fecha, hora y ubicación son obligatorias"}, 400

        fecha_hora = datetime.fromisoformat(f"{fecha}T{hora}")

        cita_prev = None
        if target_appointment and target_appointment.estado == "programada":
            cita_prev = target_appointment
        else:
            cita_prev = (
                Appointment.query
                .filter_by(tenant_id=tenant_id, prospect_id=prospect.id)
                .filter(Appointment.estado == "programada")
                .order_by(Appointment.fecha_hora.desc())
                .first()
            )

        if cita_prev:
            cita_prev.estado = "reagendada"
            cita_prev.resolved_at = datetime.now()
            cita_prev.estado_detalle = f"Reagendada para {_fmt_dt(fecha_hora)}"

        cita = Appointment(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            created_by_user_id=actor_user_id,
            fecha_hora=fecha_hora,
            ubicacion=ubicacion,
            observaciones=obs,
            estado="programada",
            estado_detalle=None,
        )
        db.session.add(cita)

        was_customer = prospect.venta_monto_sin_iva is not None or prospect.estado == "seguimiento"

        if was_customer:
            prospect.estado = "seguimiento"
        else:
            prospect.estado = "con_cita"

        if cita_prev:
            detalle = f"Cita reagendada para {_fmt_dt(fecha_hora)} en {ubicacion}"
        else:
            detalle = f"Cita programada para {_fmt_dt(fecha_hora)} en {ubicacion}"

        if target_call and target_call.estado == "pendiente":
            target_call.estado = "con_cita"
            target_call.estado_detalle = detalle
            target_call.resolved_at = datetime.now()
            _cancelar_llamadas_pendientes_de_prospecto(
                tenant_id=tenant_id,
                prospect_id=prospect.id,
                motivo="Se agendó una cita para este prospecto",
                exclude_call_id=target_call.id,
                include_followup_calls=False if was_customer else True,
            )


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
            created_by_user_id=actor_user_id,
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

        if target_call and target_call.estado in {"pendiente", "con_cita"}:           
            target_call.estado = "anexada"
            target_call.estado_detalle = f"Prospecto anexado. {detalle}"
            target_call.resolved_at = datetime.now()

        if target_appointment and target_appointment.estado == "programada":
            target_appointment.estado = "anexada"
            target_appointment.estado_detalle = f"Prospecto anexado. {detalle}"
            target_appointment.resolved_at = datetime.now()

        _cancelar_todo_agendado_de_prospecto(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            motivo=f"Prospecto anexado. {detalle}",
            appointment_estado="anexada",
            call_estado="anexada",
        )
    elif accion == "vendido":
        tipo_venta = (data.get("tipo_venta") or "").strip().lower()
        monto_con_iva = data.get("monto_con_iva")
        iva_monto = data.get("iva_monto")

        if tipo_venta not in {"contado", "credito"}:
            return {"message": "tipo_venta es obligatorio y debe ser 'contado' o 'credito'"}, 400

        if monto_con_iva is None:
            return {"message": "monto_con_iva es obligatorio"}, 400

        if iva_monto is None:
            return {"message": "iva_monto es obligatorio"}, 400

        try:
            monto_con_iva = float(monto_con_iva)
        except Exception:
            return {"message": "monto_con_iva inválido"}, 400

        try:
            iva_monto = float(iva_monto)
        except Exception:
            return {"message": "iva_monto inválido"}, 400

        if monto_con_iva <= 0:
            return {"message": "monto_con_iva debe ser mayor a 0"}, 400

        if iva_monto < 0:
            return {"message": "iva_monto no puede ser negativo"}, 400

        monto_sin_iva = monto_con_iva - iva_monto

        if monto_sin_iva <= 0:
            return {"message": "El precio sin IVA debe ser mayor a 0"}, 400

        now = datetime.now()

        venta = ProspectSale(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            created_by_user_id=visible_user_id,
            appointment_id=target_appointment.id if target_appointment else None,
            call_id=target_call.id if target_call else None,
            tipo_venta=tipo_venta,
            monto_con_iva=monto_con_iva,
            iva_monto=iva_monto,
            monto_sin_iva=monto_sin_iva,
        )
        db.session.add(venta)

        total_actual = float(prospect.venta_monto_sin_iva or 0)
        nuevo_total = total_actual + monto_sin_iva

        prospect.venta_monto_sin_iva = nuevo_total
        prospect.venta_fecha = now
        prospect.venta_tipo = tipo_venta
        prospect.estado = "seguimiento"

        tipo_label = _label_from_map(tipo_venta, VENTA_TIPO_LABELS)
        detalle = (
            f"Venta registrada ({tipo_label}) · "
            f"Venta actual sin IVA: {monto_sin_iva:.2f} · "
            f"Total acumulado: {nuevo_total:.2f}"
        )

        if target_call and target_call.estado == "pendiente":
            target_call.estado = "vendida"
            target_call.estado_detalle = detalle
            target_call.resolved_at = now

            _cancelar_todo_agendado_de_prospecto(
                tenant_id=tenant_id,
                prospect_id=prospect.id,
                motivo="Se registró una venta para este prospecto",
                appointment_estado="cancelada",
                call_estado="cancelada",
                exclude_call_id=target_call.id,
                include_followup_calls=False,
            )

        elif target_appointment and target_appointment.estado == "programada":
            target_appointment.estado = "vendida"
            target_appointment.estado_detalle = detalle
            target_appointment.resolved_at = now

            _cancelar_todo_agendado_de_prospecto(
                tenant_id=tenant_id,
                prospect_id=prospect.id,
                motivo="Se registró una venta para este prospecto",
                appointment_estado="cancelada",
                call_estado="cancelada",
                exclude_appointment_id=target_appointment.id,
                include_followup_calls=False,
            )
        else:
            cita_actual = (
                Appointment.query
                .filter_by(tenant_id=tenant_id, prospect_id=prospect.id)
                .filter(Appointment.estado == "programada")
                .order_by(Appointment.fecha_hora.desc())
                .first()
            )

            if cita_actual:
                cita_actual.estado = "vendida"
                cita_actual.estado_detalle = detalle
                cita_actual.resolved_at = now

                _cancelar_todo_agendado_de_prospecto(
                    tenant_id=tenant_id,
                    prospect_id=prospect.id,
                    motivo="Se registró una venta para este prospecto",
                    appointment_estado="cancelada",
                    call_estado="cancelada",
                    exclude_appointment_id=cita_actual.id,
                    include_followup_calls=False,
                )
            else:
                _cancelar_todo_agendado_de_prospecto(
                    tenant_id=tenant_id,
                    prospect_id=prospect.id,
                    motivo="Se registró una venta para este prospecto",
                    appointment_estado="cancelada",
                    call_estado="cancelada",
                    include_followup_calls=False,
                )

    elif accion == "iniciar_seguimiento":
        if prospect.venta_monto_sin_iva is None:
            return {"message": "No puedes iniciar seguimiento si no hay venta registrada."}, 409

        fecha = (data.get("fecha") or "").strip()
        hora = (data.get("hora") or "").strip()

        # si está pausado, esto cuenta como reanudar y SÍ pide fecha/hora
        is_resume = bool(prospect.seguimiento_pausado)

        if not fecha or not hora:
            return {"message": "Fecha y hora son obligatorias para iniciar/reanudar seguimiento."}, 400

        anchor_dt = datetime.fromisoformat(f"{fecha}T{hora}")

        prospect.estado = "seguimiento"
        prospect.seguimiento_pausado = False
        prospect.seguimiento_pausado_at = None
        prospect.seguimiento_fecha_base = anchor_dt

        _ensure_monthly_followups(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            user_id=actor_user_id,
            anchor_dt=anchor_dt,
            months=12,
            start_month_offset=0 if is_resume else 1,
        )

        if is_resume:
            detalle = f"Seguimiento reanudado. Recordatorios mensuales reprogramados desde {_fmt_dt(anchor_dt)}."
        else:
            detalle = f"Seguimiento iniciado. Recordatorios mensuales programados usando como base {_fmt_dt(anchor_dt)}."

    elif accion == "pausar_seguimiento":
        if prospect.estado != "seguimiento":
            return {"message": "Solo puedes pausar seguimiento en prospectos en seguimiento."}, 409

        prospect.seguimiento_pausado = True
        prospect.seguimiento_pausado_at = datetime.now()

        now = datetime.now()
        calls = (
            CallReminder.query
            .filter_by(tenant_id=tenant_id, prospect_id=prospect.id)
            .filter(CallReminder.estado == "pendiente")
            .filter(CallReminder.fecha_hora >= now)
            .filter(CallReminder.observaciones == FOLLOWUP_OBS)
            .all()
        )

        for c in calls:
            c.estado = "cancelada"
            c.estado_detalle = "Seguimiento pausado"
            c.resolved_at = datetime.now()

        detalle = "Seguimiento pausado. Recordatorios pendientes cancelados."

    _log_history(
        tenant_id=tenant_id,
        prospect_id=prospect.id,
        actor_user_id=actor_user_id,
        effective_user_id=visible_user_id,
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
            "accion_label": _label_from_map(h.accion, ACCION_LABELS),
            "created_at": h.created_at.isoformat(),
            "de_estado": h.de_estado,
            "de_estado_label": _label_from_map(h.de_estado, PROSPECT_ESTADO_LABELS) if h.de_estado else None,
            "a_estado": h.a_estado,
            "a_estado_label": _label_from_map(h.a_estado, PROSPECT_ESTADO_LABELS) if h.a_estado else None,
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
            "created_at": prospect.created_at.isoformat(),
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
        .filter(CallReminder.observaciones == FOLLOWUP_OBS)
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
                "proxima_llamada": dt.isoformat() if dt else None,
                "proxima_llamada_seguimiento": dt_follow.isoformat() if dt_follow else None,
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

    def g(key: str) -> int:
        return int(by_estado.get(key, 0))

    pendientes = g("pendiente")
    sin_respuesta = g("sin_respuesta")
    total_prospectos = pendientes + sin_respuesta
    total_clientes = g("seguimiento") 
    total_general = sum(by_estado.values())

    return {
        "total": total_prospectos, 
        "total_prospectos": total_prospectos,
        "total_clientes": total_clientes,
        "total_general": total_general,
        "pendientes": pendientes,
        "sin_respuesta": sin_respuesta,
        "by_estado": by_estado,
    }, 200

@prospects_bp.get("/<int:prospect_id>/detalle")
@jwt_required()
def prospect_detalle(prospect_id: int):
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

    # Historial
    Actor = aliased(User)
    Effective = aliased(User)

    historial_rows = (
        db.session.query(ProspectHistory, Actor, Effective)
        .filter(
            ProspectHistory.tenant_id == tenant_id,
            ProspectHistory.prospect_id == prospect_id,
        )
        .outerjoin(Actor, Actor.id == ProspectHistory.actor_user_id)
        .outerjoin(Effective, Effective.id == ProspectHistory.effective_user_id)
        .order_by(ProspectHistory.created_at.desc())
        .all()
    )

    historial = []
    for (h, actor_u, eff_u) in historial_rows:
        historial.append({
            "id": h.id,
            "accion": h.accion,
            "accion_label": _label_from_map(h.accion, ACCION_LABELS),
            "created_at": h.created_at.isoformat(),
            "de_estado": h.de_estado,
            "de_estado_label": _label_from_map(h.de_estado, PROSPECT_ESTADO_LABELS) if h.de_estado else None,
            "a_estado": h.a_estado,
            "a_estado_label": _label_from_map(h.a_estado, PROSPECT_ESTADO_LABELS) if h.a_estado else None,
            "detalle": h.detalle,
            "actor": {
                "id": h.actor_user_id,
                "email": actor_u.email if actor_u else None,
            },
            "effective": {
                "id": h.effective_user_id,
                "email": eff_u.email if eff_u else None,
            },
        })

    # Citas
    citas = (
        Appointment.query
        .filter_by(tenant_id=tenant_id, prospect_id=prospect.id)
        .order_by(Appointment.fecha_hora.desc())
        .all()
    )

    # Llamadas
    llamadas = (
        CallReminder.query
        .filter_by(tenant_id=tenant_id, prospect_id=prospect.id)
        .order_by(CallReminder.fecha_hora.desc())
        .all()
    )

    # Ventas
    ventas = (
        ProspectSale.query
        .filter_by(tenant_id=tenant_id, prospect_id=prospect.id)
        .order_by(ProspectSale.created_at.desc())
        .all()
    )
    # Recomendado por
    recomendado_por = None
    if prospect.recomendado_por_id:
        rp = Prospect.query.filter_by(
            id=prospect.recomendado_por_id,
            tenant_id=tenant_id,
        ).first()
        if rp:
            recomendado_por = _prospect_to_dict(rp)

    # Recomendados
    recomendados = (
        Prospect.query
        .filter_by(
            tenant_id=tenant_id,
            recomendado_por_id=prospect.id,
            assigned_to_user_id=visible_user_id,
        )
        .order_by(Prospect.created_at.desc())
        .all()
    )

    return {
        "prospecto": _prospect_to_dict(prospect),
        "resumen": {
            "recomendado_por": recomendado_por,
            "recomendados_count": len(recomendados),
            "citas_count": len(citas),
            "llamadas_count": len(llamadas),
            "ventas_count": len(ventas),
            "ventas_total_sin_iva": float(prospect.venta_monto_sin_iva or 0),
        },
        "recomendados": [_prospect_to_dict(x) for x in recomendados],
        "citas": [_appointment_to_dict(x) for x in citas],
        "llamadas": [_call_to_dict(x) for x in llamadas],
        "ventas": [_sale_to_dict(x) for x in ventas],
        "historial": historial,
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

    ESTADOS_BUSQUEDA_GLOBAL = [
        "pendiente",
        "sin_respuesta",
        "con_cita",
        "seguimiento",
        "rechazado",
    ]

    def apply_base_filters(query):
        query = query.filter(
            Prospect.tenant_id == tenant_id,
            Prospect.assigned_to_user_id == visible_user_id,
            Prospect.estado != "anexado",
        )

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

    query = apply_base_filters(Prospect.query)

    if estado and estado != "todos":
        query = query.filter(Prospect.estado == estado)

    prospectos = (
        query.order_by(Prospect.updated_at.desc(), Prospect.created_at.desc())
        .limit(limit)
        .all()
    )

    resumen_query = apply_base_filters(
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

    resumen_map = {r.estado: int(r.cantidad) for r in resumen_rows}

    return {
        "prospectos": [_prospect_to_dict(p) for p in prospectos],
        "resumen_estados": [
            {
                "estado": estado_key,
                "cantidad": resumen_map.get(estado_key, 0),
            }
            for estado_key in ESTADOS_BUSQUEDA_GLOBAL
        ],
        "total": sum(resumen_map.values()),
    }, 200