"""Capa de datos del Tablero de Seguimiento.

Todas las funciones reciben un `engine` de SQLAlchemy, así se pueden probar
con SQLite y usar en producción con PostgreSQL (Supabase).
"""
from __future__ import annotations

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
COLUMNAS = ["id", "tarea", "estado", "prioridad", "responsable", "fecha_limite"]

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
    Column("responsable", Text, nullable=False, server_default=""),
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
    """Crea la tabla `tasks` si no existe. No toca una tabla existente."""
    metadata.create_all(engine, checkfirst=True)


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
    df["responsable"] = df["responsable"].fillna("")
    return df


# --------------------------------------------------------------------------
# Escritura
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


def _validate(tarea, estado, prioridad) -> str:
    tarea = (tarea or "").strip()
    if not tarea:
        raise ValueError("El nombre de la tarea no puede estar vacío.")
    if estado not in ESTADOS:
        raise ValueError(f"Estado inválido: {estado!r}")
    if prioridad not in PRIORIDADES:
        raise ValueError(f"Prioridad inválida: {prioridad!r}")
    return tarea


def add_task(
    engine: Engine,
    tarea: str,
    estado: str = "Pendiente",
    prioridad: str = "Media",
    responsable: str = "",
    fecha_limite: Optional[date] = None,
) -> None:
    tarea = _validate(tarea, estado, prioridad)
    with engine.begin() as conn:
        conn.execute(
            insert(tasks).values(
                tarea=tarea,
                estado=estado,
                prioridad=prioridad,
                responsable=(responsable or "").strip(),
                fecha_limite=_clean(fecha_limite),
            )
        )


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
) -> dict[str, int]:
    """Aplica solo las diferencias entre `original` y `edited`.

    - filas sin `id` en `edited`        -> INSERT
    - filas cuyo `id` ya no está        -> DELETE
    - filas con `id` y valores cambiados -> UPDATE

    Solo toca los ids presentes en `original`, por lo que sirve también si la
    grilla estaba filtrada. Se valida todo antes de escribir; si algo falla no
    se guarda nada (una sola transacción).
    """
    campos = [c for c in COLUMNAS if c != "id"]
    orig = {int(r["id"]): r for _, r in original.iterrows()}

    inserts, updates, seen = [], [], set()
    for _, row in edited.iterrows():
        values = {c: _clean(row.get(c)) for c in campos}
        rid = _clean(row.get("id"))
        if rid is None and not (values["tarea"] or "").strip() and not values["responsable"] \
                and values["fecha_limite"] is None:
            continue  # fila nueva en blanco agregada sin querer
        values["tarea"] = _validate(
            values["tarea"], values["estado"] or "Pendiente", values["prioridad"] or "Media"
        )
        values["estado"] = values["estado"] or "Pendiente"
        values["prioridad"] = values["prioridad"] or "Media"
        values["responsable"] = (values["responsable"] or "").strip()

        if rid is None:
            inserts.append(values)
            continue
        rid = int(rid)
        seen.add(rid)
        if rid not in orig:
            continue  # id desconocido: no se toca
        before = {c: _clean(orig[rid][c]) for c in campos}
        before["responsable"] = (before["responsable"] or "").strip()
        if before != values:
            updates.append((rid, values))

    deletes = [i for i in orig if i not in seen]

    with engine.begin() as conn:
        for values in inserts:
            conn.execute(insert(tasks).values(**values))
        for rid, values in updates:
            conn.execute(
                update(tasks)
                .where(tasks.c.id == rid)
                .values(**values, actualizado_en=datetime.now())
            )
        if deletes:
            conn.execute(delete(tasks).where(tasks.c.id.in_(deletes)))

    return {"insertadas": len(inserts), "modificadas": len(updates), "eliminadas": len(deletes)}


def check_connection(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
