import os
from datetime import datetime
from uuid import uuid4

from flask import Blueprint, current_app, request, send_file
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import DocumentTemplate, Prospect, ProspectDocument
from ..utils.visibility import get_visible_user_id

documents_bp = Blueprint("documents", __name__)

DOC_TYPES = {
    "ine": "INE",
    "comprobante_domicilio": "Comprobante de domicilio",
    "comprobante_ingresos": "Comprobante de ingresos",
    "contrato": "Contrato",
}
ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "webp", "heic", "heif"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def _is_leader(claims):
    return (claims or {}).get("role") in {"leader", "admin"}


def _scope_user_id(claims):
    if _is_leader(claims) and not request.headers.get("X-Acting-As-User"):
        return None
    return get_visible_user_id(claims, int(get_jwt_identity()))


def _storage_root():
    root = current_app.config.get("PULSO_DOCUMENTS_DIR") or os.path.join(current_app.instance_path, "documents")
    os.makedirs(root, exist_ok=True)
    return root


def _save_upload(file, directory, prefix):
    if not file or not file.filename:
        return None, ({"message": "Archivo obligatorio"}, 400)

    filename = secure_filename(file.filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return None, ({"message": "Formato no permitido. Usa PDF o imagen."}, 400)

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > MAX_UPLOAD_BYTES:
        return None, ({"message": "Archivo demasiado grande. Máximo 15 MB."}, 400)

    os.makedirs(directory, exist_ok=True)
    stored_path = os.path.join(directory, f"{prefix}-{uuid4().hex}.{ext}")
    file.save(stored_path)
    return {
        "filename": filename,
        "stored_path": stored_path,
        "mime_type": file.mimetype,
        "size_bytes": size,
    }, None


def _active_docs_query(tenant_id, prospect_id):
    return ProspectDocument.query.filter_by(tenant_id=tenant_id, prospect_id=prospect_id).filter(ProspectDocument.deleted_at.is_(None))


def _sold_prospect_query(tenant_id, scope_user_id=None):
    q = Prospect.query.filter(Prospect.tenant_id == tenant_id).filter(Prospect.venta_monto_sin_iva.isnot(None))
    if scope_user_id:
        q = q.filter(Prospect.assigned_to_user_id == scope_user_id)
    return q


def _doc_payload(doc, can_view):
    if not doc:
        return {"uploaded": False}

    payload = {
        "uploaded": True,
        "id": doc.id if can_view else None,
        "uploaded_at": doc.created_at.isoformat() if doc.created_at else None,
        "uploaded_by": doc.uploaded_by_user.email if doc.uploaded_by_user else None,
    }
    if can_view:
        payload.update({
            "filename": doc.original_filename,
            "mime_type": doc.mime_type,
            "size_bytes": doc.size_bytes,
            "download_url": f"/documents/{doc.id}/download",
        })
    return payload


def _template_payload(doc):
    return {
        "id": doc.id,
        "name": doc.name,
        "description": doc.description,
        "filename": doc.original_filename,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "uploaded_at": doc.created_at.isoformat() if doc.created_at else None,
        "uploaded_by": doc.uploaded_by_user.email if doc.uploaded_by_user else None,
        "download_url": f"/documents/templates/{doc.id}/download",
    }


def _prospect_docs_payload(prospect, docs, can_view):
    by_type = {doc.doc_type: doc for doc in docs}
    return {
        "prospecto": {
            "id": prospect.id,
            "nombre": prospect.nombre,
            "numero": prospect.numero,
            "lada": prospect.lada_display,
            "numero_formateado": prospect.numero_formateado,
            "numero_encuesta": prospect.numero_encuesta,
            "estado": prospect.estado,
            "venta_monto_sin_iva": float(prospect.venta_monto_sin_iva or 0),
            "venta_fecha": prospect.venta_fecha.isoformat() if prospect.venta_fecha else None,
        },
        "can_view": can_view,
        "can_delete": can_view,
        "documents": {
            key: {
                "type": key,
                "label": label,
                **_doc_payload(by_type.get(key), can_view),
            }
            for key, label in DOC_TYPES.items()
        },
    }


@documents_bp.get("/types")
@jwt_required()
def document_types():
    return {"types": [{"type": key, "label": label} for key, label in DOC_TYPES.items()]}, 200


@documents_bp.get("/templates")
@jwt_required()
def list_templates():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    docs = (
        DocumentTemplate.query
        .filter_by(tenant_id=tenant_id)
        .filter(DocumentTemplate.deleted_at.is_(None))
        .order_by(DocumentTemplate.created_at.desc())
        .all()
    )

    return {
        "can_manage": _is_leader(claims),
        "templates": [_template_payload(doc) for doc in docs],
    }, 200


@documents_bp.post("/templates")
@jwt_required()
def upload_template():
    claims = get_jwt()
    if not _is_leader(claims):
        return {"message": "Solo el líder puede subir formatos."}, 403

    tenant_id = claims.get("tenant_id")
    actor_user_id = int(get_jwt_identity())
    file = request.files.get("file")
    saved, err = _save_upload(file, os.path.join(_storage_root(), str(tenant_id), "templates"), "template")
    if err:
        return err

    name = (request.form.get("name") or "").strip() or saved["filename"]
    description = (request.form.get("description") or "").strip() or None
    db.session.add(DocumentTemplate(
        tenant_id=tenant_id,
        uploaded_by_user_id=actor_user_id,
        doc_type="formato",
        name=name[:180],
        description=description,
        original_filename=saved["filename"],
        stored_path=saved["stored_path"],
        mime_type=saved["mime_type"],
        size_bytes=saved["size_bytes"],
    ))
    db.session.commit()
    return list_templates()


@documents_bp.get("/templates/<int:template_id>/download")
@jwt_required()
def download_template(template_id):
    tenant_id = get_jwt().get("tenant_id")
    doc = DocumentTemplate.query.filter_by(id=template_id, tenant_id=tenant_id).filter(DocumentTemplate.deleted_at.is_(None)).first()
    if not doc:
        return {"message": "Formato no encontrado"}, 404
    if not os.path.exists(doc.stored_path):
        return {"message": "Archivo no encontrado en almacenamiento"}, 404
    return send_file(doc.stored_path, mimetype=doc.mime_type, download_name=doc.original_filename, as_attachment=False)


@documents_bp.get("/prospects")
@jwt_required()
def list_document_prospects():
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    scope_user_id = _scope_user_id(claims)
    can_view = _is_leader(claims)
    q = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit") or 200), 500)

    query = _sold_prospect_query(tenant_id, scope_user_id).order_by(Prospect.updated_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Prospect.nombre.ilike(like), Prospect.numero.ilike(like), Prospect.numero_encuesta.ilike(like)))

    prospects = query.limit(limit).all()
    ids = [p.id for p in prospects]
    docs = []
    if ids:
        docs = ProspectDocument.query.filter(ProspectDocument.tenant_id == tenant_id, ProspectDocument.prospect_id.in_(ids)).filter(ProspectDocument.deleted_at.is_(None)).all()
    by_prospect = {}
    for doc in docs:
        by_prospect.setdefault(doc.prospect_id, []).append(doc)

    return {
        "types": [{"type": key, "label": label} for key, label in DOC_TYPES.items()],
        "items": [_prospect_docs_payload(p, by_prospect.get(p.id, []), can_view) for p in prospects],
    }, 200


@documents_bp.get("/prospects/<int:prospect_id>")
@jwt_required()
def prospect_documents(prospect_id):
    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    scope_user_id = _scope_user_id(claims)
    prospect = _sold_prospect_query(tenant_id, scope_user_id).filter(Prospect.id == prospect_id).first()
    if not prospect:
        return {"message": "Prospecto vendido no encontrado"}, 404

    docs = _active_docs_query(tenant_id, prospect.id).all()
    return _prospect_docs_payload(prospect, docs, _is_leader(claims)), 200


@documents_bp.post("/prospects/<int:prospect_id>/<doc_type>")
@jwt_required()
def upload_document(prospect_id, doc_type):
    if doc_type not in DOC_TYPES:
        return {"message": "Tipo de documento no soportado"}, 400

    claims = get_jwt()
    tenant_id = claims.get("tenant_id")
    actor_user_id = int(get_jwt_identity())
    scope_user_id = _scope_user_id(claims)
    prospect = _sold_prospect_query(tenant_id, scope_user_id).filter(Prospect.id == prospect_id).first()
    if not prospect:
        return {"message": "Solo puedes cargar documentos a prospectos vendidos."}, 404

    if _active_docs_query(tenant_id, prospect.id).filter(ProspectDocument.doc_type == doc_type).first():
        return {"message": "Este documento ya fue cargado. Pide al líder que lo borre si hay que reemplazarlo."}, 409

    saved, err = _save_upload(request.files.get("file"), os.path.join(_storage_root(), str(tenant_id), str(prospect.id)), doc_type)
    if err:
        return err

    doc = ProspectDocument(
        tenant_id=tenant_id,
        prospect_id=prospect.id,
        uploaded_by_user_id=actor_user_id,
        doc_type=doc_type,
        original_filename=saved["filename"],
        stored_path=saved["stored_path"],
        mime_type=saved["mime_type"],
        size_bytes=saved["size_bytes"],
    )
    db.session.add(doc)
    db.session.commit()

    docs = _active_docs_query(tenant_id, prospect.id).all()
    return _prospect_docs_payload(prospect, docs, _is_leader(claims)), 201


@documents_bp.get("/<int:document_id>/download")
@jwt_required()
def download_document(document_id):
    claims = get_jwt()
    if not _is_leader(claims):
        return {"message": "Solo el líder puede abrir documentos."}, 403

    tenant_id = claims.get("tenant_id")
    doc = ProspectDocument.query.filter_by(id=document_id, tenant_id=tenant_id).filter(ProspectDocument.deleted_at.is_(None)).first()
    if not doc:
        return {"message": "Documento no encontrado"}, 404
    if not os.path.exists(doc.stored_path):
        return {"message": "Archivo no encontrado en almacenamiento"}, 404

    return send_file(doc.stored_path, mimetype=doc.mime_type, download_name=doc.original_filename, as_attachment=False)


@documents_bp.delete("/<int:document_id>")
@jwt_required()
def delete_document(document_id):
    claims = get_jwt()
    if not _is_leader(claims):
        return {"message": "Solo el líder puede borrar documentos."}, 403

    tenant_id = claims.get("tenant_id")
    doc = ProspectDocument.query.filter_by(id=document_id, tenant_id=tenant_id).filter(ProspectDocument.deleted_at.is_(None)).first()
    if not doc:
        return {"message": "Documento no encontrado"}, 404

    doc.deleted_at = datetime.now()
    doc.deleted_by_user_id = int(get_jwt_identity())
    db.session.commit()
    return {"ok": True}, 200
