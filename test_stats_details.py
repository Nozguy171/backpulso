import os
from datetime import UTC, datetime
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-key-with-at-least-32-characters"

from flask_jwt_extended import create_access_token
from app import create_app
from app.extensions import db
from app.models import Prospect, ProspectSale, Tenant, User
from app.routes.stats import _appointment_item


def test_appointment_detail_includes_status_and_conclusion():
    item = _appointment_item(SimpleNamespace(
        id=1,
        prospect_id=2,
        prospect=SimpleNamespace(nombre="Cliente"),
        created_by_user=SimpleNamespace(username="asesor"),
        fecha_hora=datetime(2026, 6, 20, 10),
        ubicacion="Oficina",
        observaciones="Llevar documentos",
        estado="reagendada",
        estado_detalle="Reagendada para el lunes",
    ))

    assert item["estado_label"] == "Reagendada"
    assert item["conclusion"] == "Reagendada para el lunes"


def test_stats_dashboard_scopes_by_role_and_acting_user():
    app = create_app()

    with app.app_context():
        db.drop_all()
        db.create_all()

        team = Tenant(name="Equipo", slug="equipo")
        db.session.add(team)
        db.session.flush()

        leader = User(tenant_id=team.id, email="leader@stats.test", username="leader_stats", numero_telefonico="6861000001", password_hash="x", role="leader")
        collab = User(tenant_id=team.id, email="collab@stats.test", username="collab_stats", numero_telefonico="6861000002", password_hash="x", role="collaborator")
        db.session.add_all([leader, collab])
        db.session.flush()

        p1 = Prospect(tenant_id=team.id, created_by_user_id=leader.id, assigned_to_user_id=leader.id, nombre="Lider", numero="1", forma_obtencion_tipo="otro", forma_obtencion="Otro", estado="seguimiento")
        p2 = Prospect(tenant_id=team.id, created_by_user_id=collab.id, assigned_to_user_id=collab.id, nombre="Colab", numero="2", forma_obtencion_tipo="otro", forma_obtencion="Otro", estado="seguimiento")
        db.session.add_all([p1, p2])
        db.session.flush()

        now = datetime.now(UTC).replace(tzinfo=None)
        db.session.add_all([
            ProspectSale(tenant_id=team.id, prospect_id=p1.id, created_by_user_id=leader.id, sold_by_user_id=leader.id, effective_user_id=leader.id, tipo_venta="contado", monto_con_iva=116, iva_monto=16, monto_sin_iva=100, created_at=now),
            ProspectSale(tenant_id=team.id, prospect_id=p2.id, created_by_user_id=collab.id, sold_by_user_id=collab.id, effective_user_id=collab.id, tipo_venta="contado", monto_con_iva=232, iva_monto=32, monto_sin_iva=200, created_at=now),
        ])
        db.session.commit()

        leader_token = create_access_token(identity=str(leader.id), additional_claims={"tenant_id": team.id, "role": "leader"})
        collab_token = create_access_token(identity=str(collab.id), additional_claims={"tenant_id": team.id, "role": "collaborator"})
        client = app.test_client()

        leader_stats = client.get("/api/stats/dashboard", headers={"Authorization": f"Bearer {leader_token}"}).get_json()
        collab_stats = client.get("/api/stats/dashboard", headers={"Authorization": f"Bearer {collab_token}"}).get_json()
        acting_stats = client.get("/api/stats/dashboard", headers={"Authorization": f"Bearer {leader_token}", "X-Acting-As-User": str(collab.id)}).get_json()
        collab_details = client.get("/api/stats/details?kind=sales_month", headers={"Authorization": f"Bearer {collab_token}"}).get_json()

        assert leader_stats["kpis"]["ventas_mes_monto"] == 300
        assert collab_stats["kpis"]["ventas_mes_monto"] == 200
        assert acting_stats["kpis"]["ventas_mes_monto"] == 200
        assert [item["titulo"] for item in collab_details["items"]] == ["Colab"]


if __name__ == "__main__":
    test_appointment_detail_includes_status_and_conclusion()
    test_stats_dashboard_scopes_by_role_and_acting_user()
