import io
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-key-with-at-least-32-characters"

from flask_jwt_extended import create_access_token

from app import create_app
from app.extensions import db
from app.models import DocumentTemplate, Prospect, ProspectDocument, Tenant, User


def test_document_permissions_upload_view_delete():
    app = create_app()
    app.config["TESTING"] = True

    with tempfile.TemporaryDirectory() as tmp:
        app.config["PULSO_DOCUMENTS_DIR"] = tmp

        with app.app_context():
            db.drop_all()
            db.create_all()

            tenant = Tenant(name="Equipo", slug="equipo")
            db.session.add(tenant)
            db.session.flush()

            leader = User(tenant_id=tenant.id, email="leader@docs.test", username="leader_docs", numero_telefonico="6862000001", password_hash="x", role="leader")
            collab = User(tenant_id=tenant.id, email="collab@docs.test", username="collab_docs", numero_telefonico="6862000002", password_hash="x", role="collaborator")
            db.session.add_all([leader, collab])
            db.session.flush()

            prospect = Prospect(
                tenant_id=tenant.id,
                created_by_user_id=collab.id,
                assigned_to_user_id=collab.id,
                nombre="Cliente docs",
                numero="6861111111",
                forma_obtencion_tipo="otro",
                forma_obtencion="Evento",
                estado="seguimiento",
                venta_monto_sin_iva=100,
            )
            db.session.add(prospect)
            db.session.commit()

            leader_token = create_access_token(identity=str(leader.id), additional_claims={"tenant_id": tenant.id, "role": "leader"})
            collab_token = create_access_token(identity=str(collab.id), additional_claims={"tenant_id": tenant.id, "role": "collaborator"})
            client = app.test_client()

            upload = client.post(
                f"/api/documents/prospects/{prospect.id}/ine",
                data={"file": (io.BytesIO(b"fake image"), "ine.jpg")},
                content_type="multipart/form-data",
                headers={"Authorization": f"Bearer {collab_token}"},
            )
            assert upload.status_code == 201
            doc = ProspectDocument.query.filter_by(prospect_id=prospect.id, doc_type="ine").one()

            collab_detail = client.get(
                f"/api/documents/prospects/{prospect.id}",
                headers={"Authorization": f"Bearer {collab_token}"},
            ).get_json()
            assert collab_detail["documents"]["ine"]["uploaded"] is True
            assert collab_detail["documents"]["ine"]["id"] is None

            assert client.get(
                f"/api/documents/{doc.id}/download",
                headers={"Authorization": f"Bearer {collab_token}"},
            ).status_code == 403
            assert client.delete(
                f"/api/documents/{doc.id}",
                headers={"Authorization": f"Bearer {collab_token}"},
            ).status_code == 403

            leader_detail = client.get(
                f"/api/documents/prospects/{prospect.id}",
                headers={"Authorization": f"Bearer {leader_token}"},
            ).get_json()
            assert leader_detail["documents"]["ine"]["id"] == doc.id
            assert client.get(
                f"/api/documents/{doc.id}/download",
                headers={"Authorization": f"Bearer {leader_token}"},
            ).status_code == 200
            assert client.delete(
                f"/api/documents/{doc.id}",
                headers={"Authorization": f"Bearer {leader_token}"},
            ).status_code == 200


def test_document_templates_leader_upload_user_download():
    app = create_app()
    app.config["TESTING"] = True

    with tempfile.TemporaryDirectory() as tmp:
        app.config["PULSO_DOCUMENTS_DIR"] = tmp

        with app.app_context():
            db.drop_all()
            db.create_all()

            tenant = Tenant(name="Equipo", slug="equipo")
            db.session.add(tenant)
            db.session.flush()

            leader = User(tenant_id=tenant.id, email="leader2@docs.test", username="leader_docs_2", numero_telefonico="6862000011", password_hash="x", role="leader")
            collab = User(tenant_id=tenant.id, email="collab2@docs.test", username="collab_docs_2", numero_telefonico="6862000012", password_hash="x", role="collaborator")
            db.session.add_all([leader, collab])
            db.session.commit()

            leader_token = create_access_token(identity=str(leader.id), additional_claims={"tenant_id": tenant.id, "role": "leader"})
            collab_token = create_access_token(identity=str(collab.id), additional_claims={"tenant_id": tenant.id, "role": "collaborator"})
            client = app.test_client()

            blocked = client.post(
                "/api/documents/templates",
                data={"name": "Manual de ventas", "file": (io.BytesIO(b"manual"), "manual.pdf")},
                content_type="multipart/form-data",
                headers={"Authorization": f"Bearer {collab_token}"},
            )
            assert blocked.status_code == 403

            uploaded = client.post(
                "/api/documents/templates",
                data={"name": "Manual de ventas", "description": "", "file": (io.BytesIO(b"manual"), "manual.pdf")},
                content_type="multipart/form-data",
                headers={"Authorization": f"Bearer {leader_token}"},
            )
            assert uploaded.status_code == 200
            template = DocumentTemplate.query.filter_by(tenant_id=tenant.id).one()
            assert template.doc_type == "formato"
            assert template.name == "Manual de ventas"
            assert template.description is None

            collab_list = client.get(
                "/api/documents/templates",
                headers={"Authorization": f"Bearer {collab_token}"},
            ).get_json()
            assert collab_list["can_manage"] is False
            assert len(collab_list["templates"]) == 1
            assert collab_list["templates"][0]["name"] == "Manual de ventas"
            assert collab_list["templates"][0]["description"] is None

            assert client.get(
                f"/api/documents/templates/{template.id}/download",
                headers={"Authorization": f"Bearer {collab_token}"},
            ).status_code == 200


if __name__ == "__main__":
    test_document_permissions_upload_view_delete()
    test_document_templates_leader_upload_user_download()
