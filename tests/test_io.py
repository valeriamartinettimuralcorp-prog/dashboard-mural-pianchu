import io
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db  # noqa: E402
import io_tareas  # noqa: E402


@pytest.fixture()
def engine():
    eng = db.make_engine("sqlite:///:memory:")
    db.ensure_schema(eng)
    return eng


def test_plantilla_tiene_encabezados_e_instrucciones():
    xls = pd.ExcelFile(io.BytesIO(io_tareas.plantilla_xlsx()))
    assert xls.sheet_names == ["Tareas", "Instrucciones"]
    assert list(xls.parse("Tareas").columns) == list(io_tareas.ENCABEZADOS.values())


def test_exportar_e_importar_ida_y_vuelta(engine):
    db.add_task(engine, "A", "En Revisión", "Alta", "Ana", date(2026, 11, 5),
                responsable_validacion="v@x.com", link_formulario="https://forms.gle/a")
    original = db.load_tasks(engine)
    archivo = io_tareas.exportar_xlsx(original)
    prep = io_tareas.preparar(io_tareas.leer_archivo("t.xlsx", archivo))
    assert prep["errores"] == []
    fila = prep["validas"][0]
    assert fila["estado"] == "En Revisión" and fila["fecha_limite"] == date(2026, 11, 5)
    assert fila["id"] == int(original.loc[0, "id"])

    fila["estado"] = "Completado"
    res = db.import_tasks(engine, prep["validas"], actualizar_por_id=True)
    assert res["actualizadas"] == 1 and res["insertadas"] == 0
    assert db.load_tasks(engine).loc[0, "estado"] == "Completado"

    res = db.import_tasks(engine, prep["validas"], actualizar_por_id=False)
    assert res["insertadas"] == 1 and len(db.load_tasks(engine)) == 2


def test_csv_de_otro_tablero_con_encabezados_y_valores_distintos():
    csv = (
        "Nombre;Status;Prioridad;Responsable;Validador;Vencimiento;Notas\n"
        "Cargar remitos;en curso;alta;Pedro;jefe@x.com;31/10/2026;algo\n"
        "Cerrar caja;Cumplido;;Ana;;2026-11-02;\n"
        ";;;;;;\n"
        "Mal mail;Pendiente;Media;;no-es-mail;;\n"
        "Mala fecha;Pendiente;Media;;;32/13/2026;\n"
        "Estado raro;Congelado;Media;;;;\n"
    ).encode("latin-1")
    prep = io_tareas.preparar(io_tareas.leer_archivo("otro.csv", csv))
    assert prep["ignoradas"] == []  # «Notas» se toma como Observaciones
    assert [v["tarea"] for v in prep["validas"]] == ["Cargar remitos", "Cerrar caja"]
    v0, v1 = prep["validas"]
    assert (v0["estado"], v0["prioridad"], v0["responsable_ejecucion"]) == ("En Progreso", "Alta", "Pedro")
    assert v0["responsable_validacion"] == "jefe@x.com" and v0["fecha_limite"] == date(2026, 10, 31)
    assert v0["observaciones"] == "algo" and v1["observaciones"] == ""
    assert (v1["estado"], v1["prioridad"], v1["fecha_limite"]) == ("Completado", "Media", date(2026, 11, 2))
    assert [e["Fila"] for e in prep["errores"]] == [5, 6, 7]


def test_falta_columna_tarea():
    with pytest.raises(ValueError):
        io_tareas.preparar(pd.DataFrame({"Estado": ["Pendiente"]}))


def test_fecha_serial_excel():
    assert io_tareas._fecha(46326) == date(2026, 10, 31)


def test_observaciones_en_importacion_y_exportacion(engine):
    csv = "Tarea;Observaciones\nA;Nota inicial\nB;\n".encode()
    prep = io_tareas.preparar(io_tareas.leer_archivo("x.csv", csv))
    res = db.import_tasks(engine, prep["validas"])
    ids = {t["tarea"]: t["id"] for t in res["nuevas"]}
    obs = db.load_observaciones(engine)
    assert list(obs["texto"]) == ["Nota inicial"] and obs.loc[0, "task_id"] == ids["A"]
    db.add_observacion(engine, ids["A"], "Validado OK", "jefe@x.com")

    archivo = io_tareas.exportar_xlsx(db.load_tasks(engine), db.load_observaciones(engine))
    hoja = pd.read_excel(io.BytesIO(archivo), sheet_name="Tareas")
    texto = hoja.loc[hoja["Tarea"] == "A", "Observaciones"].iloc[0]
    assert "Importación] Nota inicial" in texto and "jefe@x.com] Validado OK" in texto
    assert texto.index("Nota inicial") < texto.index("Validado OK")
