from datetime import datetime
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt

from ..extensions import db
from ..models import Prospect, ProspectHistory, Appointment, CallReminder

prospects_bp = Blueprint("prospects", __name__)


def _log_history(
    tenant_id: int,
    prospect_id: int,
    user_id: int,
    accion: str,
    de_estado: str | None = None,
    a_estado: str | None = None,
    detalle: str | None = None,
):
    h = ProspectHistory(
        tenant_id=tenant_id,
        prospect_id=prospect_id,
        user_id=user_id,
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
        "recomendado_por_id": p.recomendado_por_id,
        "recomendado_por_nombre": p.recomendado_por.nombre if p.recomendado_por else None,
        "created_at": p.created_at.isoformat(),
    }


@prospects_bp.post("/")
@jwt_required()
def crear_prospecto():
    # 🔹 AHORA: identity es el user_id como string
    user_id = int(get_jwt_identity())
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")

    data = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    numero = (data.get("numero") or "").strip()
    observaciones = (data.get("observaciones") or "").strip() or None
    recomendado_por_id = data.get("recomendado_por_id")

    if not nombre or not numero:
        return {"message": "Nombre y número son obligatorios"}, 400

    recomendado_prospect = None
    if recomendado_por_id:
        recomendado_prospect = Prospect.query.filter_by(
            id=recomendado_por_id, tenant_id=tenant_id
        ).first()
        if not recomendado_prospect:
            return {"message": "El prospecto recomendado no existe"}, 400

    prospect = Prospect(
        tenant_id=tenant_id,
        created_by_user_id=user_id,
        nombre=nombre,
        numero=numero,
        observaciones=observaciones,
        recomendado_por=recomendado_prospect,
        estado="pendiente",
    )
    db.session.add(prospect)
    db.session.flush()  # para obtener prospect.id

    _log_history(
        tenant_id=tenant_id,
        prospect_id=prospect.id,
        user_id=user_id,
        accion="crear_prospecto",
        de_estado=None,
        a_estado="pendiente",
        detalle=observaciones,
    )

    db.session.commit()

    return {"prospecto": _prospect_to_dict(prospect)}, 201


@prospects_bp.get("/")
@jwt_required()
def listar_prospectos():
    # 🔹 igual: tomamos tenant_id de los claims
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")

    estado = request.args.get("estado")  # pendiente, sin_respuesta, etc.
    q = (request.args.get("q") or "").strip()

    query = Prospect.query.filter_by(tenant_id=tenant_id)

    if estado:
        query = query.filter_by(estado=estado)

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(Prospect.nombre.ilike(like), Prospect.numero.ilike(like))
        )

    query = query.order_by(Prospect.created_at.desc())

    return {"prospectos": [_prospect_to_dict(p) for p in query.all()]}, 200


@prospects_bp.get("/recomendadores")
@jwt_required()
def buscar_recomendadores():
    """Para autocompletar el 'Recomendado por' solo con prospectos ya existentes."""
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")

    q = (request.args.get("q") or "").strip()
    if not q:
        return {"prospectos": []}, 200

    like = f"%{q}%"
    results = (
        Prospect.query.filter_by(tenant_id=tenant_id)
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
    user_id = int(get_jwt_identity())
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")

    data = request.get_json() or {}
    accion = data.get("accion")

    prospect = Prospect.query.filter_by(id=prospect_id, tenant_id=tenant_id).first()
    if not prospect:
        return {"message": "Prospecto no encontrado"}, 404

    if accion not in {
        "sin_respuesta",
        "rechazado",
        "agendar_cita",
        "programar_llamada",
        "observaciones",
    }:
        return {"message": "Acción no soportada"}, 400

    de_estado = prospect.estado
    detalle = None

    if accion == "sin_respuesta":
        prospect.estado = "sin_respuesta"
        detalle = data.get("motivo") or "Marcado como sin respuesta"

    elif accion == "rechazado":
        prospect.estado = "rechazado"
        detalle = data.get("motivo") or "Marcado como rechazado"

    elif accion == "agendar_cita":
        fecha = data.get("fecha")  # "2025-11-20"
        hora = data.get("hora")    # "15:30"
        ubicacion = (data.get("ubicacion") or "").strip()
        obs = (data.get("observaciones") or "").strip() or None

        if not fecha or not hora or not ubicacion:
            return {"message": "Fecha, hora y ubicación son obligatorias"}, 400

        fecha_hora = datetime.fromisoformat(f"{fecha}T{hora}")
        cita = Appointment(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            created_by_user_id=user_id,
            fecha_hora=fecha_hora,
            ubicacion=ubicacion,
            observaciones=obs,
            estado="programada",
        )
        db.session.add(cita)

        prospect.estado = "con_cita"
        detalle = f"Cita programada para {fecha_hora.isoformat()} en {ubicacion}"

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
            created_by_user_id=user_id,
            fecha_hora=fecha_hora,
            observaciones=obs,
            estado="pendiente",
        )
        db.session.add(llamada)

        detalle = f"Llamada programada para {fecha_hora.isoformat()}"

    elif accion == "observaciones":
        obs = (data.get("observaciones") or "").strip()
        if not obs:
            return {"message": "Las observaciones no pueden estar vacías"}, 400

        if prospect.observaciones:
            prospect.observaciones = prospect.observaciones + "\n" + obs
        else:
            prospect.observaciones = obs

        detalle = f"Observaciones añadidas: {obs}"

    _log_history(
        tenant_id=tenant_id,
        prospect_id=prospect.id,
        user_id=user_id,
        accion=accion,
        de_estado=de_estado,
        a_estado=prospect.estado,
        detalle=detalle,
    )

    db.session.commit()

    return {"prospecto": _prospect_to_dict(prospect)}, 200
