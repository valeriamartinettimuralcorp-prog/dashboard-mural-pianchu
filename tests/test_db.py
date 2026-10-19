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
    new.update(tarea="D", estado="Pendiente", prioridad="Baja", responsable="Luis")
    import pandas as pd

    edited = pd.concat([edited, pd.DataFrame([new])], ignore_index=True)

    res = db.apply_changes(engine, original, edited)
    assert res == {"insertadas": 1, "modificadas": 1, "eliminadas": 1}
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
    bad.update(tarea="", estado="Pendiente", prioridad="Baja", responsable="x")
    import pandas as pd

    edited = pd.concat([edited, pd.DataFrame([bad])], ignore_index=True)
    with pytest.raises(ValueError):
        db.apply_changes(engine, original, edited)
    assert db.load_tasks(engine).loc[0, "estado"] == "Pendiente"  # nada se guardó


def test_normalize_url():
    assert db.normalize_url("postgres://u:p@h/db").startswith("postgresql://")


def test_no_schema_problems_on_fresh_table(engine):
    assert db.schema_problems(engine) == []
