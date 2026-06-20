import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-key-with-at-least-32-characters"

from flask_jwt_extended import create_access_token

from app import create_app
from app.extensions import db
from app.models import Prospect, Tenant, User


def test_survey_number_follows_acquisition_type():
    app = create_app()
    app.config["TESTING"] = True

    with app.app_context():
        db.create_all()
        tenant = Tenant(name="Test", slug="test")
        db.session.add(tenant)
        db.session.flush()
        user = User(
            tenant_id=tenant.id,
            email="test@example.com",
            username="test",
            numero_telefonico="6861234567",
            password_hash="test",
            role="leader",
        )
        db.session.add(user)
        db.session.commit()
        token = create_access_token(
            identity=str(user.id),
            additional_claims={"tenant_id": tenant.id, "role": user.role},
        )
        headers = {"Authorization": f"Bearer {token}"}
        client = app.test_client()

        for index, acquisition in enumerate(("cita_en_frio", "otro"), 1):
            response = client.post(
                "/api/prospects/",
                json={
                    "nombre": f"Sin encuesta {index}",
                    "numero": f"686000000{index}",
                    "forma_obtencion_tipo": acquisition,
                    "forma_obtencion": "Evento" if acquisition == "otro" else None,
                },
                headers=headers,
            )
            assert response.status_code == 201
            assert Prospect.query.filter_by(nombre=f"Sin encuesta {index}").one().numero_encuesta is None

        response = client.post(
            "/api/prospects/",
            json={
                "nombre": "Encuestado",
                "numero": "6860000002",
                "forma_obtencion_tipo": "encuesta",
            },
            headers=headers,
        )
        assert response.status_code == 400


if __name__ == "__main__":
    test_survey_number_follows_acquisition_type()
