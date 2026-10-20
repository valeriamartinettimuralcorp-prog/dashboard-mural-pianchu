import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db  # noqa: E402


@pytest.fixture()
def engine():
    eng = db.make_engine("sqlite:///:memory:")
    db.ensure_schema(eng)
    return eng


def test_add_and_load(engine):
    db.add_task(engine, "  Llamar a cliente ", "Pendiente", "Alta", "Ana", date(2026, 11, 1))
    df = db.load_tasks(engine)
    assert len(df) == 1
    assert df.loc[0, "tarea"] == "Llamar a cliente"
    assert df.loc[0, "fecha_limite"] == date(2026, 11, 1)


def test_add_validates(engine):
    with pytest.raises(ValueError):
        db.add_task(engine, "   ")
    with pytest.raises(ValueError):
        db.add_task(engine, "x", estado="Otro")


def test_set_estado(engine):
    db.add_task(engine, "A")
    tid = int(db.load_tasks(engine).loc[0, "id"])
    db.set_estado(engine, tid, "Completado")
    assert db.load_tasks(engine).loc[0, "estado"] == "Completado"


def test_apply_changes_insert_update_delete(engine):
    for n in ("A", "B", "C"):
        db.add_task(engine, n)
    original = db.load_tasks(engine)[db.COLUMNAS]
    edited = original.copy()
    edited = edited[edited["tarea"] != "C"]  # borrar C
    edited.loc[edited["tarea"] == "A", "estado"] = "En Progreso"  # modificar A
    new = {c: None for c in db.COLUMNAS}
    new.update(tarea="D", estado="Pendiente", prioridad="Baja", responsable_ejecucion="Luis")
    import pandas as pd

    edited = pd.concat([edited, pd.DataFrame([new])], ignore_index=True)

    res = db.apply_changes(engine, original, edited)
    nuevas = res.pop("nuevas")
    assert res == {"insertadas": 1, "modificadas": 1, "eliminadas": 1}
    assert nuevas[0]["tarea"] == "D" and nuevas[0]["id"]
    out = db.load_tasks(engine).set_index("tarea")
    assert set(out.index) == {"A", "B", "D"}
    assert out.loc["A", "estado"] == "En Progreso"
    assert out["id"].notna().all()  # el id se autogenera (el bug anterior lo perdía)


def test_apply_changes_with_filtered_view_keeps_other_rows(engine):
    for n in ("A", "B", "C"):
        db.add_task(engine, n)
    full = db.load_tasks(engine)[db.COLUMNAS]
    view = full[full["tarea"] == "A"]  # grilla filtrada
    db.apply_changes(engine, view, view)  # sin cambios
    assert len(db.load_tasks(engine)) == 3
    db.apply_changes(engine, view, view.iloc[0:0])  # borrar A desde la vista
    assert set(db.load_tasks(engine)["tarea"]) == {"B", "C"}


def test_apply_changes_is_atomic(engine):
    db.add_task(engine, "A")
    original = db.load_tasks(engine)[db.COLUMNAS]
    edited = original.copy()
    edited.loc[0, "estado"] = "En Progreso"
    bad = {c: None for c in db.COLUMNAS}
    bad.update(tarea="", estado="Pendiente", prioridad="Baja", responsable_ejecucion="x")
    import pandas as pd

    edited = pd.concat([edited, pd.DataFrame([bad])], ignore_index=True)
    with pytest.raises(ValueError):
        db.apply_changes(engine, original, edited)
    assert db.load_tasks(engine).loc[0, "estado"] == "Pendiente"  # nada se guardó


def test_normalize_url():
    want = "postgresql+psycopg://u:p@h/db"
    assert db.normalize_url("postgres://u:p@h/db") == want
    assert db.normalize_url("postgresql://u:p@h/db") == want
    assert db.normalize_url("postgresql+psycopg2://u:p@h/db") == want
    assert db.normalize_url("sqlite:///:memory:") == "sqlite:///:memory:"


def test_no_schema_problems_on_fresh_table(engine):
    assert db.schema_problems(engine) == []


def test_add_task_returns_id_and_new_fields(engine):
    t = db.add_task(
        engine, "Validar stock", responsable_ejecucion="Ana",
        responsable_validacion=" Jefe@Empresa.com ", link_formulario="forms.gle/abc",
    )
    assert t["id"]
    df = db.load_tasks(engine)
    assert df.loc[0, "responsable_validacion"] == "jefe@empresa.com"
    assert df.loc[0, "link_formulario"] == "https://forms.gle/abc"


def test_email_invalido(engine):
    with pytest.raises(ValueError):
        db.add_task(engine, "x", responsable_validacion="no-es-mail")


def test_migra_tabla_con_columna_responsable_vieja():
    from sqlalchemy import text

    eng = db.make_engine("sqlite:///:memory:")
    with eng.begin() as c:
        c.execute(text(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, tarea TEXT NOT NULL, "
            "estado TEXT NOT NULL DEFAULT 'Pendiente', prioridad TEXT NOT NULL DEFAULT 'Media', "
            "responsable TEXT NOT NULL DEFAULT '', fecha_limite DATE, "
            "creado_en DATETIME, actualizado_en DATETIME)"
        ))
        c.execute(text("INSERT INTO tasks (tarea, responsable) VALUES ('Vieja', 'Pedro')"))
    db.ensure_schema(eng)
    db.ensure_schema(eng)  # idempotente
    assert db.schema_problems(eng) == []
    df = db.load_tasks(eng)
    assert df.loc[0, "responsable_ejecucion"] == "Pedro"
    assert df.loc[0, "responsable_validacion"] == ""


def test_mail_de_notificacion():
    import notify

    cfg = {"host": "smtp.x", "user": "bot@x.com", "password": "p"}
    assert notify.smtp_configured(cfg) and not notify.smtp_configured({})
    msg = notify.build_message(cfg, {
        "tarea": "Cierre <mes>", "responsable_validacion": "jefe@x.com",
        "responsable_ejecucion": "Ana", "prioridad": "Alta", "estado": "Pendiente",
        "fecha_limite": date(2026, 11, 1), "link_formulario": "https://forms.gle/abc",
    }, "https://tablero.app")
    assert msg["To"] == "jefe@x.com"
    html = msg.get_body("html").get_content()
    assert "https://forms.gle/abc" in html and "&lt;mes&gt;" in html and "01/11/2026" in html


def test_observaciones_historial(engine):
    t = db.add_task(engine, "Validar caja", responsable_validacion="jefe@x.com")
    db.add_observacion(engine, t["id"], "  Falta comprobante  ", "jefe@x.com")
    db.add_observacion(engine, t["id"], "Comprobante recibido, OK", "jefe@x.com")
    obs = db.load_observaciones(engine, t["id"])
    assert list(obs["texto"]) == ["Comprobante recibido, OK", "Falta comprobante"]  # más nueva primero
    assert db.contar_observaciones(engine) == {t["id"]: 2}
    with pytest.raises(ValueError):
        db.add_observacion(engine, t["id"], "   ")
    with pytest.raises(ValueError):
        db.add_observacion(engine, 9999, "x")
    db.delete_observacion(engine, int(obs.loc[0, "id"]))
    assert len(db.load_observaciones(engine, t["id"])) == 1


def test_borrar_tarea_borra_sus_observaciones(engine):
    t = db.add_task(engine, "A")
    db.add_observacion(engine, t["id"], "nota")
    original = db.load_tasks(engine)[db.COLUMNAS]
    db.apply_changes(engine, original, original.iloc[0:0])
    assert db.load_observaciones(engine).empty
