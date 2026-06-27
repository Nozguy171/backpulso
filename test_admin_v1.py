import os
from datetime import datetime

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-key-with-at-least-32-characters"
os.environ["PULSO_ADMIN_ENABLED"] = "1"

from flask_jwt_extended import create_access_token

from app import create_app
from app.extensions import db
from app.models import AdminAudit, Prospect, ProspectSale, Tenant, User


def test_admin_permissions_and_sales_attribution():
    app = create_app()
    app.config["TESTING"] = True

    with app.app_context():
        db.create_all()
        team = Tenant(name="Equipo", slug="equipo")
        db.session.add(team)
        db.session.flush()
        leader = User(tenant_id=team.id, email="leader@test.com", username="leader", numero_telefonico="6860000001", password_hash="x", role="leader", is_platform_admin=True)
        collaborator = User(tenant_id=team.id, email="collab@test.com", username="collab", numero_telefonico="6860000002", password_hash="x", role="collaborator")
        db.session.add_all([leader, collaborator])
        db.session.flush()
        prospect = Prospect(tenant_id=team.id, created_by_user_id=leader.id, assigned_to_user_id=collaborator.id, nombre="Cliente", numero="6861111111", forma_obtencion_tipo="otro", forma_obtencion="Evento", estado="seguimiento")
        db.session.add(prospect)
        db.session.flush()
        db.session.add_all([
            ProspectSale(tenant_id=team.id, prospect_id=prospect.id, created_by_user_id=leader.id, sold_by_user_id=leader.id, effective_user_id=leader.id, tipo_venta="contado", monto_con_iva=116, iva_monto=16, monto_sin_iva=100),
            ProspectSale(tenant_id=team.id, prospect_id=prospect.id, created_by_user_id=leader.id, sold_by_user_id=collaborator.id, effective_user_id=collaborator.id, tipo_venta="credito", monto_con_iva=232, iva_monto=32, monto_sin_iva=200),
        ])
        db.session.commit()

        admin_token = create_access_token(identity=str(leader.id), additional_claims={"tenant_id": team.id, "role": "leader"})
        regular_token = create_access_token(identity=str(collaborator.id), additional_claims={"tenant_id": team.id, "role": "collaborator"})
        client = app.test_client()

        assert client.get("/api/admin/dashboard", headers={"Authorization": f"Bearer {regular_token}"}).status_code == 403
        response = client.get(f"/api/admin/teams/{team.id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["team"]["ventas_equipo"] == 300
        assert payload["team"]["ventas_lider"] == 100
        assert payload["team"]["ventas_colaboradores"] == 200
        assert payload["sales"][0]["capturada_por"]["id"] == leader.id
        assert payload["prospects"][0]["nombre"] == "Cliente"

        today = datetime.now().strftime("%Y-%m-%d")
        response = client.get(f"/api/admin/dashboard?period={today}", headers={"Authorization": f"Bearer {admin_token}"})
        assert response.get_json()["dashboard"]["total_vendido"] == 300

        response = client.post(f"/api/admin/users/{collaborator.id}/reset-password", json={"password": "new-password"}, headers={"Authorization": f"Bearer {admin_token}"})
        assert response.status_code == 200
        assert db.session.get(User, collaborator.id).check_password("new-password")
        assert AdminAudit.query.filter_by(action="password_reset").count() == 1
        assert client.get("/api/admin/prospects", headers={"Authorization": f"Bearer {admin_token}"}).get_json()["prospects"][0]["nombre"] == "Cliente"
        assert client.get("/api/admin/audit", headers={"Authorization": f"Bearer {admin_token}"}).get_json()["audit"][0]["action_label"] == "Contraseña restablecida"


if __name__ == "__main__":
    test_admin_permissions_and_sales_attribution()
