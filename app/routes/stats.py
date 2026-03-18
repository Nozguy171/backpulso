from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from flask import Blueprint, request
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from sqlalchemy import func, distinct, extract

from ..extensions import db
from ..models import Prospect, Appointment, CallReminder, User

stats_bp = Blueprint("stats", __name__)

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


def _pct(num: int, den: int) -> float:
    return round((num / den) * 100, 2) if den else 0.0


def _month_range(year: int, month: int):
    start = datetime(year, month, 1)
    end = start + relativedelta(months=1)
    return start, end


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default

@stats_bp.get("/dashboard")
@jwt_required()
def dashboard_stats():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")

    now = datetime.now()

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
        return q.filter(Prospect.tenant_id == tenant_id)

    # =========================
    # KPI 1: ventas del mes
    # =========================
    current_month_start = datetime(now.year, now.month, 1)
    next_month_start = current_month_start + relativedelta(months=1)
    prev_month_start = current_month_start - relativedelta(months=1)

    ventas_mes_query = apply_prospect_scope(
        db.session.query(
            func.count(Prospect.id).label("ventas"),
            func.coalesce(func.sum(Prospect.venta_monto_sin_iva), 0).label("monto"),
        )
    ).filter(
        Prospect.venta_fecha.isnot(None),
        Prospect.venta_fecha >= current_month_start,
        Prospect.venta_fecha < next_month_start,
    ).first()

    ventas_mes_count = int(ventas_mes_query.ventas or 0)
    ventas_mes_monto = float(ventas_mes_query.monto or 0)

    ventas_prev_query = apply_prospect_scope(
        db.session.query(
            func.count(Prospect.id).label("ventas"),
            func.coalesce(func.sum(Prospect.venta_monto_sin_iva), 0).label("monto"),
        )
    ).filter(
        Prospect.venta_fecha.isnot(None),
        Prospect.venta_fecha >= prev_month_start,
        Prospect.venta_fecha < current_month_start,
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
    available_years_rows = apply_prospect_scope(
        db.session.query(extract("year", Prospect.venta_fecha).label("year"))
    ).filter(
        Prospect.venta_fecha.isnot(None)
    ).distinct().order_by(extract("year", Prospect.venta_fecha).asc()).all()

    available_years = [int(r.year) for r in available_years_rows if r.year is not None]

    if sales_granularity == "year":
        ventas_rows = apply_prospect_scope(
            db.session.query(
                extract("year", Prospect.venta_fecha).label("period"),
                func.count(Prospect.id).label("ventas"),
                func.coalesce(func.sum(Prospect.venta_monto_sin_iva), 0).label("monto"),
            )
        ).filter(
            Prospect.venta_fecha.isnot(None)
        ).group_by(
            extract("year", Prospect.venta_fecha)
        ).order_by(
            extract("year", Prospect.venta_fecha).asc()
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
        ventas_rows = apply_prospect_scope(
            db.session.query(
                extract("month", Prospect.venta_fecha).label("period"),
                func.count(Prospect.id).label("ventas"),
                func.coalesce(func.sum(Prospect.venta_monto_sin_iva), 0).label("monto"),
            )
        ).filter(
            Prospect.venta_fecha.isnot(None),
            extract("year", Prospect.venta_fecha) == sales_year,
        ).group_by(
            extract("month", Prospect.venta_fecha)
        ).order_by(
            extract("month", Prospect.venta_fecha).asc()
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
    week_start = datetime.combine((now.date() - timedelta(days=6)), datetime.min.time())
    week_end = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())

    calls_week_q = (
        db.session.query(
            func.date(CallReminder.created_at).label("day"),
            func.count(CallReminder.id).label("count"),
        )
        .join(Prospect, Prospect.id == CallReminder.prospect_id)
        .filter(CallReminder.tenant_id == tenant_id)
        .filter(CallReminder.created_at >= week_start, CallReminder.created_at < week_end)
    )
    calls_week_rows = calls_week_q.group_by(func.date(CallReminder.created_at)).all()

    appts_week_q = (
        db.session.query(
            func.date(Appointment.created_at).label("day"),
            func.count(Appointment.id).label("count"),
        )
        .join(Prospect, Prospect.id == Appointment.prospect_id)
        .filter(Appointment.tenant_id == tenant_id)
        .filter(Appointment.created_at >= week_start, Appointment.created_at < week_end)
    )
    appts_week_rows = appts_week_q.group_by(func.date(Appointment.created_at)).all()

    calls_by_day = {str(r.day): int(r.count) for r in calls_week_rows}
    appts_by_day = {str(r.day): int(r.count) for r in appts_week_rows}

    actividad_semanal = []
    for i in range(7):
        d = (week_start + timedelta(days=i)).date()
        d_key = str(d)
        actividad_semanal.append({
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
        collab_start, collab_end = _month_range(collab_year, collab_month)

    users_q = (
        User.query
        .filter_by(tenant_id=tenant_id)
        .filter(User.role.in_(["leader", "collaborator"]))
    )

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