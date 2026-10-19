"""Capa de datos del Tablero de Seguimiento.

Todas las funciones reciben un `engine` de SQLAlchemy, así se pueden probar
con SQLite y usar en producción con PostgreSQL (Supabase).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

import pandas as pd
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    delete,
    func,
    inspect,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine

ESTADOS = ["Pendiente", "En Progreso", "En Revisión", "Completado"]
PRIORIDADES = ["Baja", "Media", "Alta", "Urgente"]
COLUMNAS = [
    "id",
    "tarea",
    "estado",
    "prioridad",
    "responsable_ejecucion",
    "responsable_validacion",
    "link_formulario",
    "fecha_limite",
]
# Campos de texto opcionales: se guardan como "" en vez de NULL.
TEXTOS = ["responsable_ejecucion", "responsable_validacion", "link_formulario"]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

metadata = MetaData()

tasks = Table(
    "tasks",
    metadata,
    Column(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("tarea", Text, nullable=False),
    Column("estado", Text, nullable=False, server_default="Pendiente"),
    Column("prioridad", Text, nullable=False, server_default="Media"),
    Column("responsable_ejecucion", Text, nullable=False, server_default=""),
    Column("responsable_validacion", Text, nullable=False, server_default=""),
    Column("link_formulario", Text, nullable=False, server_default=""),
    Column("fecha_limite", Date, nullable=True),
    Column("creado_en", DateTime, server_default=func.now()),
    Column("actualizado_en", DateTime, server_default=func.now()),
)


# --------------------------------------------------------------------------
# Conexión y esquema
# --------------------------------------------------------------------------
def normalize_url(url: str) -> str:
    """Deja la URL lista para SQLAlchemy usando el driver `psycopg` (v3).

    Acepta `postgres://`, `postgresql://` o `postgresql+<driver>://` (lo que
    entregue Supabase o se haya pegado en los secretos) y las unifica.
    """
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    if url.startswith("postgresql+"):
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    return url  # sqlite u otros (tests)


def make_engine(url: str) -> Engine:
    url = normalize_url(url)
    kwargs = {"pool_pre_ping": True, "pool_recycle": 300}
    if url.startswith("postgresql+psycopg://"):
        # El pooler de Supabase (puerto 6543) no admite prepared statements.
        kwargs["connect_args"] = {"prepare_threshold": None}
    return create_engine(url, **kwargs)


def ensure_schema(engine: Engine) -> None:
    """Crea la tabla si no existe y agrega las columnas nuevas si faltan.

    Los cambios sobre una tabla existente son solo aditivos: renombrar
    `responsable` -> `responsable_ejecucion` y agregar columnas de texto.
    """
    metadata.create_all(engine, checkfirst=True)
    cols = {c["name"] for c in inspect(engine).get_columns("tasks")}
    stmts = []
    if "responsable" in cols and "responsable_ejecucion" not in cols:
        stmts.append("ALTER TABLE tasks RENAME COLUMN responsable TO responsable_ejecucion")
        cols.add("responsable_ejecucion")
    for col in TEXTOS:
        if col not in cols:
            stmts.append(f"ALTER TABLE tasks ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    if stmts:
        with engine.begin() as conn:
            for s in stmts:
                conn.execute(text(s))


def schema_problems(engine: Engine) -> list[str]:
    """Detecta una tabla creada con el código viejo (to_sql replace)."""
    insp = inspect(engine)
    if not insp.has_table("tasks"):
        return []
    problems = []
    if not insp.get_pk_constraint("tasks").get("constrained_columns"):
        problems.append("`tasks` no tiene clave primaria en `id`.")
    cols = {c["name"]: c for c in insp.get_columns("tasks")}
    missing = [c for c in COLUMNAS if c not in cols]
    if missing:
        problems.append(f"Faltan columnas: {', '.join(missing)}.")
    elif "date" not in str(cols["fecha_limite"]["type"]).lower():
        problems.append("`fecha_limite` es texto y debería ser DATE.")
    if engine.dialect.name == "postgresql" and "id" in cols:
        if cols["id"].get("default") is None and not cols["id"].get("autoincrement"):
            problems.append("`id` no se autogenera (falta secuencia/identity).")
    return problems


# --------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------
def load_tasks(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        df = pd.read_sql(
            select(*[tasks.c[c] for c in COLUMNAS]).order_by(tasks.c.id), conn
        )
    df["fecha_limite"] = pd.to_datetime(df["fecha_limite"]).dt.date
    for c in TEXTOS:
        df[c] = df[c].fillna("")
    return df


# --------------------------------------------------------------------------
# Validación
# --------------------------------------------------------------------------
def _clean(value):
    """Convierte NaN/NaT/None de pandas en None; el resto queda igual."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.date()
    return value


def normalize_link(link: Optional[str]) -> str:
    link = (link or "").strip()
    if link and not re.match(r"^https?://", link, re.I):
        link = "https://" + link
    return link


def _validate(values: dict) -> dict:
    """Valida y normaliza los campos de una tarea. Devuelve una copia."""
    v = dict(values)
    v["tarea"] = (v.get("tarea") or "").strip()
    if not v["tarea"]:
        raise ValueError("El nombre de la tarea no puede estar vacío.")
    v["estado"] = v.get("estado") or "Pendiente"
    v["prioridad"] = v.get("prioridad") or "Media"
    if v["estado"] not in ESTADOS:
        raise ValueError(f"Estado inválido: {v['estado']!r}")
    if v["prioridad"] not in PRIORIDADES:
        raise ValueError(f"Prioridad inválida: {v['prioridad']!r}")
    for c in TEXTOS:
        v[c] = (v.get(c) or "").strip()
    v["responsable_validacion"] = v["responsable_validacion"].lower()
    if v["responsable_validacion"] and not EMAIL_RE.match(v["responsable_validacion"]):
        raise ValueError(
            f"El responsable de validación debe ser un mail válido "
            f"(tarea «{v['tarea']}»: {v['responsable_validacion']!r})."
        )
    v["link_formulario"] = normalize_link(v["link_formulario"])
    v["fecha_limite"] = _clean(v.get("fecha_limite"))
    return v


# --------------------------------------------------------------------------
# Escritura
# --------------------------------------------------------------------------
def add_task(
    engine: Engine,
    tarea: str,
    estado: str = "Pendiente",
    prioridad: str = "Media",
    responsable_ejecucion: str = "",
    fecha_limite: Optional[date] = None,
    responsable_validacion: str = "",
    link_formulario: str = "",
) -> dict:
    """Inserta la tarea y la devuelve (con su `id`)."""
    values = _validate(
        {
            "tarea": tarea,
            "estado": estado,
            "prioridad": prioridad,
            "responsable_ejecucion": responsable_ejecucion,
            "responsable_validacion": responsable_validacion,
            "link_formulario": link_formulario,
            "fecha_limite": fecha_limite,
        }
    )
    with engine.begin() as conn:
        res = conn.execute(insert(tasks).values(**values))
        values["id"] = int(res.inserted_primary_key[0])
    return values


def set_estado(engine: Engine, task_id: int, estado: str) -> None:
    if estado not in ESTADOS:
        raise ValueError(f"Estado inválido: {estado!r}")
    with engine.begin() as conn:
        conn.execute(
            update(tasks)
            .where(tasks.c.id == int(task_id))
            .values(estado=estado, actualizado_en=datetime.now())
        )


def apply_changes(
    engine: Engine, original: pd.DataFrame, edited: pd.DataFrame
) -> dict:
    """Aplica solo las diferencias entre `original` y `edited`.

    - filas sin `id` en `edited`        -> INSERT
    - filas cuyo `id` ya no está        -> DELETE
    - filas con `id` y valores cambiados -> UPDATE

    Solo toca los ids presentes en `original`, por lo que sirve también si la
    grilla estaba filtrada. Se valida todo antes de escribir; si algo falla no
    se guarda nada (una sola transacción).

    Devuelve los conteos y, en `nuevas`, las tareas insertadas (con `id`)
    para poder notificar a los responsables.
    """
    campos = [c for c in COLUMNAS if c != "id"]
    orig = {int(r["id"]): r for _, r in original.iterrows()}

    inserts, updates, seen = [], [], set()
    for _, row in edited.iterrows():
        raw = {c: _clean(row.get(c)) for c in campos}
        rid = _clean(row.get("id"))
        if rid is None and not (raw["tarea"] or "").strip() \
                and not any(raw[c] for c in TEXTOS) and raw["fecha_limite"] is None:
            continue  # fila nueva en blanco agregada sin querer
        values = _validate(raw)

        if rid is None:
            inserts.append(values)
            continue
        rid = int(rid)
        seen.add(rid)
        if rid not in orig:
            continue  # id desconocido: no se toca
        before = {c: _clean(orig[rid][c]) for c in campos}
        for c in TEXTOS:
            before[c] = (before[c] or "").strip()
        if before != values:
            updates.append((rid, values))

    deletes = [i for i in orig if i not in seen]

    nuevas = []
    with engine.begin() as conn:
        for values in inserts:
            res = conn.execute(insert(tasks).values(**values))
            nuevas.append({**values, "id": int(res.inserted_primary_key[0])})
        for rid, values in updates:
            conn.execute(
                update(tasks)
                .where(tasks.c.id == rid)
                .values(**values, actualizado_en=datetime.now())
            )
        if deletes:
            conn.execute(delete(tasks).where(tasks.c.id.in_(deletes)))

    return {
        "insertadas": len(inserts),
        "modificadas": len(updates),
        "eliminadas": len(deletes),
        "nuevas": nuevas,
    }


def check_connection(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
