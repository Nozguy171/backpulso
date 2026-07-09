import os
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-key-with-at-least-32-characters"

from flask_jwt_extended import create_access_token

from app import create_app
from app.extensions import db
from app.models import Appointment, CallReminder, Prospect, ProspectSale, Tenant, User


def make_app_with_user():
    app = create_app()
    app.config["TESTING"] = True

    with app.app_context():
        db.create_all()
        tenant = Tenant(name="Equipo", slug="equipo")
        db.session.add(tenant)
        db.session.flush()

        user = User(
            tenant_id=tenant.id,
            email="leader@test.com",
            username="leader",
            numero_telefonico="6860000001",
            password_hash="x",
            role="leader",
        )
        db.session.add(user)
        db.session.flush()

    return app, tenant.id, user.id


def test_pending_prospect_call_cannot_be_marked_done():
    app, tenant_id, user_id = make_app_with_user()

    with app.app_context():
        prospect = Prospect(
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            assigned_to_user_id=user_id,
            nombre="Cliente",
            numero="6861111111",
            forma_obtencion_tipo="otro",
            forma_obtencion="Evento",
            estado="pendiente",
        )
        db.session.add(prospect)
        db.session.flush()

        call = CallReminder(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            created_by_user_id=user_id,
            fecha_hora=datetime.now() + timedelta(hours=2),
            estado="pendiente",
        )
        db.session.add(call)
        db.session.commit()

        token = create_access_token(
            identity=str(user_id),
            additional_claims={"tenant_id": tenant_id, "role": "leader"},
        )
        client = app.test_client()
        headers = {"Authorization": f"Bearer {token}"}

        response = client.post(f"/api/calls/{call.id}/marcar-hecha", json={}, headers=headers)
        assert response.status_code == 409
        assert db.session.get(CallReminder, call.id).estado == "pendiente"

        prospect.estado = "con_cita"
        db.session.commit()

        response = client.post(f"/api/calls/{call.id}/marcar-hecha", json={}, headers=headers)
        assert response.status_code == 200
        assert db.session.get(CallReminder, call.id).estado == "hecha"


def test_prospect_detail_includes_only_result_calls():
    app, tenant_id, user_id = make_app_with_user()

    with app.app_context():
        prospect = Prospect(
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            assigned_to_user_id=user_id,
            nombre="Cliente",
            numero="6861111112",
            forma_obtencion_tipo="otro",
            forma_obtencion="Evento",
            estado="pendiente",
        )
        db.session.add(prospect)
        db.session.flush()

        pending_call = CallReminder(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            created_by_user_id=user_id,
            fecha_hora=datetime.now() + timedelta(hours=2),
            estado="pendiente",
        )
        done_call = CallReminder(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            created_by_user_id=user_id,
            fecha_hora=datetime.now() - timedelta(hours=2),
            estado="hecha",
            estado_detalle="Llamada realizada",
        )
        db.session.add_all([pending_call, done_call])
        db.session.commit()

        token = create_access_token(
            identity=str(user_id),
            additional_claims={"tenant_id": tenant_id, "role": "leader"},
        )
        response = app.test_client().get(
            f"/api/prospects/{prospect.id}/detalle",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["resumen"]["llamadas_count"] == 1
        assert payload["llamadas"][0]["id"] == done_call.id
        assert payload["llamadas"][0]["estado"] == "hecha"


def test_direct_result_actions_create_result_calls():
    app, tenant_id, user_id = make_app_with_user()

    with app.app_context():
        prospect = Prospect(
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            assigned_to_user_id=user_id,
            nombre="Cliente cita",
            numero="6861111113",
            forma_obtencion_tipo="otro",
            forma_obtencion="Evento",
            estado="pendiente",
        )
        sold_prospect = Prospect(
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            assigned_to_user_id=user_id,
            nombre="Cliente venta",
            numero="6861111114",
            forma_obtencion_tipo="otro",
            forma_obtencion="Evento",
            estado="pendiente",
        )
        db.session.add_all([prospect, sold_prospect])
        db.session.commit()

        token = create_access_token(
            identity=str(user_id),
            additional_claims={"tenant_id": tenant_id, "role": "leader"},
        )
        client = app.test_client()
        headers = {"Authorization": f"Bearer {token}"}

        response = client.post(
            f"/api/prospects/{prospect.id}/acciones",
            json={
                "accion": "agendar_cita",
                "fecha": "2026-07-01",
                "hora": "10:00",
                "ubicacion": "Oficina",
                "ubicacion_lat": 32.5149,
                "ubicacion_lng": -117.0382,
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert CallReminder.query.filter_by(prospect_id=prospect.id).one().estado == "con_cita"
        cita = Appointment.query.filter_by(prospect_id=prospect.id).one()
        assert cita.ubicacion_lat == 32.5149
        assert cita.ubicacion_lng == -117.0382
        assert db.session.get(Prospect, prospect.id).ultima_ubicacion_cita == "Oficina"
        assert response.get_json()["prospecto"]["ultima_ubicacion_cita"] == "Oficina"

        response = client.post(
            f"/api/prospects/{sold_prospect.id}/acciones",
            json={
                "accion": "vendido",
                "tipo_venta": "contado",
                "monto_con_iva": 116,
                "iva_monto": 16,
                "fecha": "2026-07-02",
                "hora": "11:00",
            },
            headers=headers,
        )
        assert response.status_code == 200

        response = client.get(
            f"/api/prospects/{sold_prospect.id}/detalle",
            headers=headers,
        )
        payload = response.get_json()
        assert [call["estado"] for call in payload["llamadas"]] == ["vendida"]


def test_can_sell_again_from_realized_appointment():
    app, tenant_id, user_id = make_app_with_user()

    with app.app_context():
        prospect = Prospect(
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            assigned_to_user_id=user_id,
            nombre="Cliente reventa",
            numero="6861111115",
            forma_obtencion_tipo="otro",
            forma_obtencion="Evento",
            estado="seguimiento",
            venta_monto_sin_iva=100,
        )
        db.session.add(prospect)
        db.session.flush()
        cita = Appointment(
            tenant_id=tenant_id,
            prospect_id=prospect.id,
            created_by_user_id=user_id,
            fecha_hora=datetime.now() - timedelta(hours=1),
            ubicacion="Oficina",
            estado="realizada",
        )
        db.session.add(cita)
        db.session.commit()

        token = create_access_token(
            identity=str(user_id),
            additional_claims={"tenant_id": tenant_id, "role": "leader"},
        )
        response = app.test_client().post(
            f"/api/prospects/{prospect.id}/acciones",
            json={
                "accion": "vendido",
                "appointment_id": cita.id,
                "tipo_venta": "contado",
                "monto_con_iva": 232,
                "iva_monto": 32,
                "fecha": "2026-07-03",
                "hora": "12:00",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert db.session.get(Appointment, cita.id).estado == "vendida"
        assert float(db.session.get(Prospect, prospect.id).venta_monto_sin_iva) == 300
        assert ProspectSale.query.filter_by(prospect_id=prospect.id, appointment_id=cita.id).count() == 1


if __name__ == "__main__":
    test_pending_prospect_call_cannot_be_marked_done()
    test_prospect_detail_includes_only_result_calls()
    test_direct_result_actions_create_result_calls()
    test_can_sell_again_from_realized_appointment()
