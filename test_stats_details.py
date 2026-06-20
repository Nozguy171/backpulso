import os
from datetime import datetime
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-key-with-at-least-32-characters"

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


if __name__ == "__main__":
    test_appointment_detail_includes_status_and_conclusion()
