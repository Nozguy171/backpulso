from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from sqlalchemy import func, distinct, extract

from ..extensions import db
from ..models import Prospect, Appointment, CallReminder, User, ProspectSale
from ..utils.visibility import get_visible_user_id

stats_bp = Blueprint("stats", __name__)

LOCAL_TZ = ZoneInfo("America/Tijuana")
UTC = ZoneInfo("UTC")
MONTHS_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
WEEKDAYS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

ESTADO_LABELS = {
    "pendiente": "Pendiente",
    "sin_respuesta": "Sin respuesta",
    "con_cita": "Con cita",
    "seguimiento": "Seguimiento",
    "rechazado": "Rechazado",
}

ESTADO_COLORS = {
    "pendiente": "#3b82f6",
    "sin_respuesta": "#f59e0b",
    "con_cita": "#8b5cf6",
    "seguimiento": "#10b981",
    "rechazado": "#ef4444",
}

ACTIVITY_STATUS_LABELS = {
    "programada": "Pendiente",
    "pendiente": "Pendiente",
    "realizada": "Realizada",
    "hecha": "Realizada",
    "reagendada": "Reagendada",
    "cancelada": "Cancelada",
    "vendida": "Venta",
    "rechazada": "Rechazada",
    "con_cita": "Cita agendada",
    "sin_respuesta": "Sin respuesta",
    "anexada": "Anexada",
}


def _pct(num: int, den: int) -> float:
    return round((num / den) * 100, 2) if den else 0.0


def _month_range(year: int, month: int):
    start = datetime(year, month, 1)
    end = start + relativedelta(months=1)
    return start, end


def _local_now():
    return datetime.now(LOCAL_TZ)


def _utc_naive(local_dt):
    return local_dt.astimezone(UTC).replace(tzinfo=None)


def _local_day_range(day: str):
    d = datetime.fromisoformat(day).date()
    start = datetime.combine(d, datetime.min.time(), tzinfo=LOCAL_TZ)
    return _utc_naive(start), _utc_naive(start + timedelta(days=1))


def _local_month_range(year: int, month: int):
    start = datetime(year, month, 1, tzinfo=LOCAL_TZ)
    return _utc_naive(start), _utc_naive(start + relativedelta(months=1))


def _local_date(dt):
    return dt.replace(tzinfo=UTC).astimezone(LOCAL_TZ).date() if dt else None


def _local_iso(dt):
    return dt.replace(tzinfo=UTC).astimezone(LOCAL_TZ).replace(tzinfo=None).isoformat() if dt else None


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default

def _stats_scope_user_id(claims):
    actor_user_id = int(get_jwt_identity())
    role = (claims or {}).get("role")
    if role in ("leader", "admin") and not request.headers.get("X-Acting-As-User"):
        return None
    return get_visible_user_id(claims, actor_user_id)

def _sale_item(s: ProspectSale):
    tipo_venta = (s.tipo_venta or "venta").title()
    return {
        "id": s.id,
        "tipo": "Venta",
        "titulo": s.prospect.nombre if s.prospect else f"Venta #{s.id}",
        "fecha": _local_iso(s.created_at),
        "detalle": f"{tipo_venta} - ${float(s.monto_sin_iva or 0):,.2f} sin IVA · Usuario: {s.sold_by_user.username if s.sold_by_user else 'Sin usuario'}",
        "prospect_id": s.prospect_id,
        "trato_prospecto": s.prospect.trato_prospecto if s.prospect else None,
    }

def _prospect_item(p: Prospect, tipo="Prospecto"):
    u = db.session.get(User, p.assigned_to_user_id)
    return {
        "id": p.id,
        "tipo": tipo,
        "titulo": p.nombre,
        "fecha": _local_iso(p.created_at),
        "detalle": f"{p.numero_formateado} · Encuesta: {p.numero_encuesta or '—'} · {p.estado} · Usuario: {u.username if u else 'Sin usuario'}",
        "prospect_id": p.id,
        "trato_prospecto": p.trato_prospecto,
    }

def _call_item(c: CallReminder):
    p = db.session.get(Prospect, c.prospect_id)
    u = db.session.get(User, c.created_by_user_id)
    return {
        "id": c.id,
        "tipo": "Llamada",
        "titulo": p.nombre if p else f"Llamada #{c.id}",
        "fecha": c.fecha_hora.isoformat() if c.fecha_hora else None,
        "detalle": f"{c.observaciones or 'Sin observaciones'} · Usuario: {u.username if u else 'Sin usuario'}",
        "estado": c.estado,
        "estado_label": ACTIVITY_STATUS_LABELS.get(c.estado, c.estado.replace("_", " ").capitalize()),
        "conclusion": c.estado_detalle,
        "prospect_id": c.prospect_id,
        "trato_prospecto": p.trato_prospecto if p else None,
    }

def _appointment_item(a: Appointment):
    return {
        "id": a.id,
        "tipo": "Cita",
        "titulo": a.prospect.nombre if getattr(a, "prospect", None) else f"Cita #{a.id}",
        "fecha": a.fecha_hora.isoformat() if a.fecha_hora else None,
        "detalle": f"{a.ubicacion} · {a.observaciones or 'Sin observaciones'} · Usuario: {a.created_by_user.username if a.created_by_user else 'Sin usuario'}",
        "ubicacion_lat": getattr(a, "ubicacion_lat", None),
        "ubicacion_lng": getattr(a, "ubicacion_lng", None),
        "estado": a.estado,
        "estado_label": ACTIVITY_STATUS_LABELS.get(a.estado, a.estado.replace("_", " ").capitalize()),
        "conclusion": a.estado_detalle,
        "prospect_id": a.prospect_id,
        "trato_prospecto": a.prospect.trato_prospecto if getattr(a, "prospect", None) else None,
    }

def _date_range_from_day(day: str):
    return _local_day_range(day)

@stats_bp.get("/details")
@jwt_required()
def stats_details():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    scope_user_id = _stats_scope_user_id(claims)
    kind = (request.args.get("kind") or "").strip()
    limit = min(_safe_int(request.args.get("limit"), 100), 300)
    now = _local_now()

    items = []
    title = "Detalle"

    def apply_prospect_scope(q):
        q = q.filter(Prospect.tenant_id == tenant_id)
        return q.filter(Prospect.assigned_to_user_id == scope_user_id) if scope_user_id else q

    def apply_sale_scope(q):
        q = q.filter(ProspectSale.tenant_id == tenant_id)
        return q.filter(ProspectSale.effective_user_id == scope_user_id) if scope_user_id else q

    def apply_appointment_scope(q):
        q = q.join(Prospect, Prospect.id == Appointment.prospect_id).filter(Appointment.tenant_id == tenant_id)
        return q.filter(Prospect.assigned_to_user_id == scope_user_id) if scope_user_id else q

    def apply_call_scope(q):
        q = q.join(Prospect, Prospect.id == CallReminder.prospect_id).filter(CallReminder.tenant_id == tenant_id)
        return q.filter(Prospect.assigned_to_user_id == scope_user_id) if scope_user_id else q

    if kind == "sales_month":
        start, end = _local_month_range(now.year, now.month)
        rows = apply_sale_scope(ProspectSale.query).filter(ProspectSale.created_at >= start, ProspectSale.created_at < end).order_by(ProspectSale.created_at.desc()).limit(limit).all()
        title = "Ventas del mes"
        items = [_sale_item(x) for x in rows]

    elif kind == "sales_period":
        granularity = request.args.get("granularity") or "month"
        period = request.args.get("period") or ""
        q = apply_sale_scope(ProspectSale.query)
        if granularity == "year":
            q = q.filter(extract("year", ProspectSale.created_at) == _safe_int(period, now.year))
            title = f"Ventas {period}"
        else:
            year = _safe_int(request.args.get("year"), now.year)
            month = (MONTHS_ES.index(period) + 1) if period in MONTHS_ES else now.month
            start, end = _local_month_range(year, month)
            q = q.filter(ProspectSale.created_at >= start, ProspectSale.created_at < end)
            title = f"Ventas {period} {year}"
        items = [_sale_item(x) for x in q.order_by(ProspectSale.created_at.desc()).limit(limit).all()]

    elif kind == "status":
        estado = request.args.get("estado") or ""
        rows = apply_prospect_scope(Prospect.query).filter(Prospect.estado == estado).order_by(Prospect.created_at.desc()).limit(limit).all()
        title = f"Prospectos: {ESTADO_LABELS.get(estado, estado)}"
        items = [_prospect_item(x) for x in rows]

    elif kind == "with_appointment":
        rows = apply_appointment_scope(Appointment.query).order_by(Appointment.fecha_hora.desc()).limit(limit).all()
        title = "Citas agendadas"
        items = [_appointment_item(x) for x in rows]

    elif kind == "sold_with_appointment":
        rows = (apply_prospect_scope(Prospect.query).join(Appointment, Appointment.prospect_id == Prospect.id).filter(Prospect.venta_monto_sin_iva.isnot(None)).distinct().order_by(Prospect.venta_fecha.desc()).limit(limit).all())
        title = "Vendidos con cita"
        items = [_prospect_item(x, "Venta") for x in rows]

    elif kind == "calls_done":
        rows = apply_call_scope(CallReminder.query).filter(CallReminder.estado == "hecha").order_by(CallReminder.fecha_hora.desc()).limit(limit).all()
        title = "Llamadas realizadas"
        items = [_call_item(x) for x in rows]

    elif kind == "collaborator":
        user_id = _safe_int(request.args.get("user_id"), 0)
        if scope_user_id:
            user_id = scope_user_id
        metric = request.args.get("metric") or "vendidos"
        collab_mode = (request.args.get("collab_mode") or "always").strip().lower()
        collab_year = _safe_int(request.args.get("collab_year"), now.year)
        collab_month = _safe_int(request.args.get("collab_month"), now.month)
        q = Prospect.query.filter_by(tenant_id=tenant_id, assigned_to_user_id=user_id)
        if collab_mode == "month":
            start, end = _local_month_range(collab_year, collab_month)
            q = q.filter(Prospect.created_at >= start, Prospect.created_at < end)
        if metric == "vendidos":
            q = q.filter(Prospect.venta_monto_sin_iva.isnot(None))
            title = "Ventas del colaborador"
        elif metric == "citas":
            q = q.join(Appointment, Appointment.prospect_id == Prospect.id).distinct()
            title = "Citas del colaborador"
        else:
            title = "Prospectos del colaborador"
        items = [_prospect_item(x) for x in q.order_by(Prospect.created_at.desc()).limit(limit).all()]

    elif kind == "week_activity":
        day = request.args.get("day") or now.date().isoformat()
        metric = request.args.get("metric") or "llamadas"
        start, end = _date_range_from_day(day)
        if metric == "citas":
            rows = apply_appointment_scope(Appointment.query).filter(Appointment.created_at >= start, Appointment.created_at < end).order_by(Appointment.created_at.desc()).limit(limit).all()
            title = f"Citas creadas {day}"
            items = [_appointment_item(x) for x in rows]
        else:
            rows = apply_call_scope(CallReminder.query).filter(CallReminder.created_at >= start, CallReminder.created_at < end).order_by(CallReminder.created_at.desc()).limit(limit).all()
            title = f"Llamadas creadas {day}"
            items = [_call_item(x) for x in rows]

    return {"title": title, "items": items}, 200

@stats_bp.get("/dashboard")
@jwt_required()
def dashboard_stats():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    scope_user_id = _stats_scope_user_id(claims)

    now = _local_now()

    sales_year = _safe_int(request.args.get("sales_year"), now.year)
    sales_granularity = (request.args.get("sales_granularity") or "month").strip().lower()
    if sales_granularity not in {"month", "year"}:
        sales_granularity = "month"

    collab_mode = (request.args.get("collab_mode") or "always").strip().lower()
    if collab_mode not in {"always", "month"}:
        collab_mode = "always"

    collab_year = _safe_int(request.args.get("collab_year"), now.year)
    collab_month_raw = request.args.get("collab_month")
    collab_month = _safe_int(collab_month_raw, now.month) if collab_month_raw and collab_month_raw != "all" else now.month
    if collab_month < 1 or collab_month > 12:
        collab_month = now.month

    def apply_prospect_scope(q):
        q = q.filter(Prospect.tenant_id == tenant_id)
        return q.filter(Prospect.assigned_to_user_id == scope_user_id) if scope_user_id else q
    
    def apply_sale_scope(q):
        q = q.filter(ProspectSale.tenant_id == tenant_id)
        return q.filter(ProspectSale.effective_user_id == scope_user_id) if scope_user_id else q
    # =========================
    # KPI 1: ventas del mes
    # =========================
    current_month_start, next_month_start = _local_month_range(now.year, now.month)
    prev_month_start = _utc_naive(datetime(now.year, now.month, 1, tzinfo=LOCAL_TZ) - relativedelta(months=1))

    ventas_mes_query = apply_sale_scope(
        db.session.query(
            func.count(ProspectSale.id).label("ventas"),
            func.coalesce(func.sum(ProspectSale.monto_sin_iva), 0).label("monto"),
        )
    ).filter(
        ProspectSale.created_at >= current_month_start,
        ProspectSale.created_at < next_month_start,
    ).first()

    ventas_mes_count = int(ventas_mes_query.ventas or 0)
    ventas_mes_monto = float(ventas_mes_query.monto or 0)

    ventas_prev_query = apply_sale_scope(
        db.session.query(
            func.count(ProspectSale.id).label("ventas"),
            func.coalesce(func.sum(ProspectSale.monto_sin_iva), 0).label("monto"),
        )
    ).filter(
        ProspectSale.created_at >= prev_month_start,
        ProspectSale.created_at < current_month_start,
    ).first()

    ventas_prev_monto = float(ventas_prev_query.monto or 0)

    if ventas_prev_monto > 0:
        ventas_mes_delta_pct = round(((ventas_mes_monto - ventas_prev_monto) / ventas_prev_monto) * 100, 2)
    else:
        ventas_mes_delta_pct = 100.0 if ventas_mes_monto > 0 else 0.0

    # =========================
    # KPI 2: citas sobre prospectos totales
    # =========================
    total_prospectos = apply_prospect_scope(
        db.session.query(func.count(Prospect.id))
    ).scalar() or 0

    prospectos_con_cita = apply_prospect_scope(
        db.session.query(func.count(distinct(Prospect.id)))
        .select_from(Prospect)
        .join(Appointment, Appointment.prospect_id == Prospect.id)
    ).scalar() or 0

    tasa_citas_pct = _pct(int(prospectos_con_cita), int(total_prospectos))

    # =========================
    # KPI 3: conversión sobre prospectos con cita
    # =========================
    prospectos_vendidos_con_cita = apply_prospect_scope(
        db.session.query(func.count(distinct(Prospect.id)))
        .select_from(Prospect)
        .join(Appointment, Appointment.prospect_id == Prospect.id)
    ).filter(
        Prospect.venta_monto_sin_iva.isnot(None)
    ).scalar() or 0

    tasa_conversion_pct = _pct(int(prospectos_vendidos_con_cita), int(prospectos_con_cita))

    # =========================
    # KPI 4: llamadas realizadas
    # =========================
    llamadas_realizadas_q = (
        db.session.query(func.count(CallReminder.id))
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .filter(CallReminder.tenant_id == tenant_id)
        .filter(Prospect.assigned_to_user_id == scope_user_id if scope_user_id else True)
        .filter(CallReminder.estado == "hecha")
    )

    llamadas_realizadas = int(llamadas_realizadas_q.scalar() or 0)

    # =========================
    # Distribución de prospectos
    # quitar anexado
    # =========================
    distribucion_rows = apply_prospect_scope(
        db.session.query(Prospect.estado, func.count(Prospect.id))
    ).filter(
        Prospect.estado != "anexado"
    ).group_by(Prospect.estado).all()

    distribucion_prospectos = [
        {
            "estado": ESTADO_LABELS.get(estado, estado.title()),
            "cantidad": int(cantidad),
            "color": ESTADO_COLORS.get(estado, "#94a3b8"),
            "estado_key": estado,
        }
        for estado, cantidad in distribucion_rows
    ]

    # =========================
    # Evolución de ventas
    # month => meses de un año
    # year => años completos
    # =========================
    available_years_rows = apply_sale_scope(
        db.session.query(extract("year", ProspectSale.created_at).label("year"))
    ).distinct().order_by(extract("year", ProspectSale.created_at).asc()).all()
    available_years = [int(r.year) for r in available_years_rows if r.year is not None]

    if sales_granularity == "year":
        ventas_rows = apply_sale_scope(
            db.session.query(
                extract("year", ProspectSale.created_at).label("period"),
                func.count(ProspectSale.id).label("ventas"),
                func.coalesce(func.sum(ProspectSale.monto_sin_iva), 0).label("monto"),
            )
        ).group_by(
            extract("year", ProspectSale.created_at)
        ).order_by(
            extract("year", ProspectSale.created_at).asc()
        ).all()

        ventas_chart = [
            {
                "periodo": str(int(r.period)),
                "ventas": int(r.ventas),
                "monto": float(r.monto or 0),
            }
            for r in ventas_rows
        ]
    else:
        ventas_rows = apply_sale_scope(
            db.session.query(
                extract("month", ProspectSale.created_at).label("period"),
                func.count(ProspectSale.id).label("ventas"),
                func.coalesce(func.sum(ProspectSale.monto_sin_iva), 0).label("monto"),
            )
        ).filter(
            extract("year", ProspectSale.created_at) == sales_year,
        ).group_by(
            extract("month", ProspectSale.created_at)
        ).order_by(
            extract("month", ProspectSale.created_at).asc()
        ).all()

        by_month = {
            int(r.period): {
                "ventas": int(r.ventas),
                "monto": float(r.monto or 0),
            }
            for r in ventas_rows
        }

        ventas_chart = [
            {
                "periodo": MONTHS_ES[m - 1],
                "ventas": by_month.get(m, {}).get("ventas", 0),
                "monto": by_month.get(m, {}).get("monto", 0),
            }
            for m in range(1, 13)
        ]

    # =========================
    # Actividad semanal real
    # llamadas/citas creadas en últimos 7 días
    # =========================
    week_start_local = datetime.combine(now.date() - timedelta(days=6), datetime.min.time(), tzinfo=LOCAL_TZ)
    week_start = _utc_naive(week_start_local)
    week_end = _utc_naive(week_start_local + timedelta(days=7))

    calls_week_rows = (
        db.session.query(CallReminder.created_at)
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .filter(CallReminder.tenant_id == tenant_id)
        .filter(Prospect.assigned_to_user_id == scope_user_id if scope_user_id else True)
        .filter(CallReminder.created_at >= week_start, CallReminder.created_at < week_end)
        .all()
    )

    appts_week_rows = (
        db.session.query(Appointment.created_at)
        .join(Prospect, Prospect.id == Appointment.prospect_id)
        .filter(Appointment.tenant_id == tenant_id)
        .filter(Prospect.assigned_to_user_id == scope_user_id if scope_user_id else True)
        .filter(Appointment.created_at >= week_start, Appointment.created_at < week_end)
        .all()
    )

    calls_by_day = {}
    for r in calls_week_rows:
        k = str(_local_date(r.created_at))
        calls_by_day[k] = calls_by_day.get(k, 0) + 1

    appts_by_day = {}
    for r in appts_week_rows:
        k = str(_local_date(r.created_at))
        appts_by_day[k] = appts_by_day.get(k, 0) + 1

    actividad_semanal = []
    for i in range(7):
        d = (week_start_local + timedelta(days=i)).date()
        d_key = str(d)
        actividad_semanal.append({
            "day": d_key,
            "dia": WEEKDAYS_ES[d.weekday()],
            "llamadas": calls_by_day.get(d_key, 0),
            "citas": appts_by_day.get(d_key, 0),
        })

    llamadas_semana_total = sum(x["llamadas"] for x in actividad_semanal)
    llamadas_promedio_dia = round(llamadas_semana_total / 7, 2)

    # =========================
    # Rendimiento por colaborador
    # always => toda la historia
    # month => prospectos creados en ese mes
    # =========================
    collab_start = None
    collab_end = None
    if collab_mode == "month":
        collab_start, collab_end = _local_month_range(collab_year, collab_month)

    users_q = (
        User.query
        .filter_by(tenant_id=tenant_id)
        .filter(User.role.in_(["leader", "collaborator"]))
    )
    if scope_user_id:
        users_q = users_q.filter(User.id == scope_user_id)

    users = users_q.order_by(User.username.asc()).all()

    colaboradores = []
    for u in users:
        prospects_q = Prospect.query.filter(
            Prospect.tenant_id == tenant_id,
            Prospect.assigned_to_user_id == u.id,
        )

        if collab_mode == "month":
            prospects_q = prospects_q.filter(
                Prospect.created_at >= collab_start,
                Prospect.created_at < collab_end,
            )

        total_u = int(prospects_q.count())

        con_cita_u_q = (
            db.session.query(func.count(distinct(Prospect.id)))
            .select_from(Prospect)
            .join(Appointment, Appointment.prospect_id == Prospect.id)
            .filter(
                Prospect.tenant_id == tenant_id,
                Prospect.assigned_to_user_id == u.id,
            )
        )
        if collab_mode == "month":
            con_cita_u_q = con_cita_u_q.filter(
                Prospect.created_at >= collab_start,
                Prospect.created_at < collab_end,
            )

        con_cita_u = int(con_cita_u_q.scalar() or 0)

        vendidos_u_q = Prospect.query.filter(
            Prospect.tenant_id == tenant_id,
            Prospect.assigned_to_user_id == u.id,
            Prospect.venta_monto_sin_iva.isnot(None),
        )
        if collab_mode == "month":
            vendidos_u_q = vendidos_u_q.filter(
                Prospect.created_at >= collab_start,
                Prospect.created_at < collab_end,
            )

        vendidos_u = int(vendidos_u_q.count())

        colaboradores.append({
            "user_id": u.id,
            "nombre": u.username or u.email,
            "email": u.email,
            "prospectos": total_u,
            "prospectos_con_cita": con_cita_u,
            "vendidos": vendidos_u,
            "tasa_citas": _pct(con_cita_u, total_u),
            "tasa_conversion": _pct(vendidos_u, con_cita_u),
        })

    top_performer = None
    if colaboradores:
        top_performer = max(
            colaboradores,
            key=lambda x: (x["vendidos"], x["tasa_conversion"], x["prospectos_con_cita"])
        )

    return {
        "kpis": {
            "ventas_mes_count": ventas_mes_count,
            "ventas_mes_monto": ventas_mes_monto,
            "ventas_mes_delta_pct": ventas_mes_delta_pct,
            "total_prospectos": int(total_prospectos),
            "prospectos_con_cita": int(prospectos_con_cita),
            "prospectos_vendidos_con_cita": int(prospectos_vendidos_con_cita),
            "tasa_citas_pct": tasa_citas_pct,
            "tasa_conversion_pct": tasa_conversion_pct,
            "llamadas_realizadas": llamadas_realizadas,
            "llamadas_promedio_dia": llamadas_promedio_dia,
        },
        "ventas_chart": {
            "granularity": sales_granularity,
            "year": sales_year,
            "available_years": available_years,
            "data": ventas_chart,
        },
        "distribucion_prospectos": distribucion_prospectos,
        "actividad_semanal": actividad_semanal,
        "colaboradores": colaboradores,
        "top_performer": top_performer,
        "collab_mode": collab_mode,
        "collab_year": collab_year,
        "collab_month": collab_month if collab_mode == "month" else None,
    }, 200
