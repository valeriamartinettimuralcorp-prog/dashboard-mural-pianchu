import hmac
import os
from datetime import date

import pandas as pd
import streamlit as st

import db

st.set_page_config(page_title="Tablero de Seguimiento", page_icon="📋", layout="wide")


# --------------------------------------------------------------------------
# Configuración / acceso
# --------------------------------------------------------------------------
def secret(path, default=None):
    """Lee un secreto anidado sin romper si no existe secrets.toml."""
    try:
        node = st.secrets
        for key in path:
            node = node[key]
        return node
    except Exception:
        return default


def check_password() -> bool:
    """Si hay `app_password` en los secretos, exige iniciar sesión."""
    expected = secret(["app_password"])
    if not expected:
        return True
    if st.session_state.get("autenticado"):
        return True
    st.title("📋 Tablero de Seguimiento")
    with st.form("login"):
        pwd = st.text_input("Contraseña", type="password")
        if st.form_submit_button("Ingresar"):
            if hmac.compare_digest(pwd.encode(), str(expected).encode()):
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
    return False


if not check_password():
    st.stop()


@st.cache_resource
def get_engine():
    url = secret(["connections", "postgresql", "url"]) or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Falta la URL de la base. Configurala en .streamlit/secrets.toml "
            "([connections.postgresql] url = ...) o en la variable DATABASE_URL."
        )
    engine = db.make_engine(url)
    db.check_connection(engine)
    db.ensure_schema(engine)
    return engine


try:
    engine = get_engine()
except Exception as e:
    get_engine.clear()
    st.error(f"No se pudo conectar con la base de datos: {e}")
    st.info("Revisá `.streamlit/secrets.toml` (o los Secrets en Streamlit Cloud). Ver README.")
    st.stop()

problems = db.schema_problems(engine)
if problems:
    st.error("La tabla `tasks` tiene el esquema de la versión anterior y hay que migrarla:")
    for p in problems:
        st.markdown(f"- {p}")
    st.markdown(
        "Ejecutá la sección **B) Migración** de `schema.sql` en el SQL Editor de "
        "Supabase (hacé un backup antes) y recargá la página."
    )
    st.stop()


def run(action, ok_msg=None):
    """Ejecuta una acción de escritura mostrando errores sin tirar la app."""
    try:
        result = action()
    except Exception as ex:
        st.error(f"Error: {ex}")
        return None
    if ok_msg:
        st.session_state["flash"] = ok_msg(result) if callable(ok_msg) else ok_msg
    return result if result is not None else True


# --------------------------------------------------------------------------
# Encabezado
# --------------------------------------------------------------------------
head_l, head_r = st.columns([6, 1])
head_l.title("📋 Tablero de Seguimiento")
if head_r.button("🔄 Recargar", width="stretch"):
    st.rerun()
if msg := st.session_state.pop("flash", None):
    st.success(msg)

# --------------------------------------------------------------------------
# Nueva tarea
# --------------------------------------------------------------------------
st.sidebar.header("➕ Nueva Tarea")
with st.sidebar.form("new_task_form", clear_on_submit=True):
    nueva_tarea = st.text_input("Nombre de la tarea")
    estado_nuevo = st.selectbox("Estado", db.ESTADOS)
    prioridad_nueva = st.selectbox("Prioridad", db.PRIORIDADES, index=1)
    responsable_nuevo = st.text_input("Responsable")
    fecha_nueva = st.date_input("Fecha límite", value=date.today())
    submitted = st.form_submit_button("Agregar Tarea")

if submitted:
    if not nueva_tarea.strip():
        st.sidebar.error("Poné un nombre para la tarea.")
    elif run(
        lambda: db.add_task(
            engine, nueva_tarea, estado_nuevo, prioridad_nueva, responsable_nuevo, fecha_nueva
        ),
        "¡Tarea agregada!",
    ):
        st.rerun()

# --------------------------------------------------------------------------
# Datos y filtros
# --------------------------------------------------------------------------
try:
    df = db.load_tasks(engine)
except Exception as e:
    st.error(f"No se pudieron leer las tareas: {e}")
    st.stop()

hoy = date.today()
df["vencida"] = df.apply(
    lambda r: r["fecha_limite"] is not None
    and not pd.isna(r["fecha_limite"])
    and r["fecha_limite"] < hoy
    and r["estado"] != "Completado",
    axis=1,
)

st.sidebar.markdown("---")
st.sidebar.header("🔎 Filtros")
buscar = st.sidebar.text_input("Buscar en el nombre")
resp_opts = sorted({r for r in df["responsable"] if r})
f_resp = st.sidebar.multiselect("Responsable", resp_opts)
f_prio = st.sidebar.multiselect("Prioridad", db.PRIORIDADES)
solo_vencidas = st.sidebar.checkbox("Solo vencidas")
ocultar_completadas = st.sidebar.checkbox("Ocultar completadas")

fdf = df
if buscar:
    fdf = fdf[fdf["tarea"].str.contains(buscar, case=False, na=False)]
if f_resp:
    fdf = fdf[fdf["responsable"].isin(f_resp)]
if f_prio:
    fdf = fdf[fdf["prioridad"].isin(f_prio)]
if solo_vencidas:
    fdf = fdf[fdf["vencida"]]
if ocultar_completadas:
    fdf = fdf[fdf["estado"] != "Completado"]
filtrado = len(fdf) != len(df)
if filtrado:
    st.caption(f"Mostrando {len(fdf)} de {len(df)} tareas (filtros activos).")

tab1, tab2, tab3 = st.tabs(["📊 Tabla Editable", "📌 Tablero Kanban", "⚙️ Resumen"])

# --------------------------------------------------------------------------
# Tabla editable
# --------------------------------------------------------------------------
with tab1:
    st.subheader("Grilla de tareas (edición directa)")
    st.caption(
        "Editá celdas, agregá filas al final o borrá filas seleccionadas; "
        "después tocá **Guardar cambios**."
    )
    base = fdf[db.COLUMNAS].reset_index(drop=True)
    edited = st.data_editor(
        base,
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        key="task_editor",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "tarea": st.column_config.TextColumn("Tarea", required=True),
            "estado": st.column_config.SelectboxColumn(
                "Estado", options=db.ESTADOS, required=True, default="Pendiente"
            ),
            "prioridad": st.column_config.SelectboxColumn(
                "Prioridad", options=db.PRIORIDADES, required=True, default="Media"
            ),
            "responsable": st.column_config.TextColumn("Responsable"),
            "fecha_limite": st.column_config.DateColumn("Fecha límite", format="DD/MM/YYYY"),
        },
    )
    if st.button("💾 Guardar cambios", type="primary"):
        res = run(
            lambda: db.apply_changes(engine, base, edited),
            lambda r: (
                f"Guardado: {r['insertadas']} nuevas, {r['modificadas']} modificadas, "
                f"{r['eliminadas']} eliminadas."
            ),
        )
        if res:
            st.rerun()

# --------------------------------------------------------------------------
# Kanban
# --------------------------------------------------------------------------
with tab2:
    st.subheader("Tablero Kanban")
    if fdf.empty:
        st.info("No hay tareas para mostrar.")
    else:
        cols = st.columns(len(db.ESTADOS))
        for idx, estado in enumerate(db.ESTADOS):
            sub = fdf[fdf["estado"] == estado]
            with cols[idx]:
                st.markdown(f"### {estado} ({len(sub)})")
                for _, row in sub.iterrows():
                    tid = int(row["id"])
                    with st.container(border=True):
                        st.markdown(f"**{row['tarea']}**")
                        st.caption(
                            f"👤 {row['responsable'] or 'Sin asignar'} · ⚡ {row['prioridad']}"
                        )
                        if pd.notna(row["fecha_limite"]) and row["fecha_limite"] is not None:
                            label = row["fecha_limite"].strftime("%d/%m/%Y")
                            st.caption(f"{'🔴 Vencida · ' if row['vencida'] else '📅 '}{label}")
                        b1, b2 = st.columns(2)
                        if idx > 0 and b1.button("◀", key=f"prev_{tid}", help=f"Mover a {db.ESTADOS[idx-1]}"):
                            if run(lambda: db.set_estado(engine, tid, db.ESTADOS[idx - 1])):
                                st.rerun()
                        if idx < len(db.ESTADOS) - 1 and b2.button("▶", key=f"next_{tid}", help=f"Mover a {db.ESTADOS[idx+1]}"):
                            if run(lambda: db.set_estado(engine, tid, db.ESTADOS[idx + 1])):
                                st.rerun()

# --------------------------------------------------------------------------
# Resumen
# --------------------------------------------------------------------------
with tab3:
    st.subheader("Estadísticas y exportación")
    if df.empty:
        st.info("Todavía no hay tareas.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", len(df))
        c2.metric("Completadas", int((df["estado"] == "Completado").sum()))
        c3.metric("Pendientes / En proceso", int(df["estado"].isin(["Pendiente", "En Progreso"]).sum()))
        c4.metric("Vencidas", int(df["vencida"].sum()))

        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Tareas por estado**")
            st.bar_chart(df["estado"].value_counts().reindex(db.ESTADOS, fill_value=0))
        with g2:
            st.markdown("**Tareas abiertas por responsable**")
            abiertas = df[df["estado"] != "Completado"]
            por_resp = abiertas["responsable"].replace("", "Sin asignar").value_counts()
            if por_resp.empty:
                st.caption("No hay tareas abiertas.")
            else:
                st.bar_chart(por_resp)

        st.markdown("---")
        st.download_button(
            "⬇️ Descargar CSV",
            data=df[db.COLUMNAS].to_csv(index=False).encode("utf-8-sig"),
            file_name="tareas_seguimiento.csv",
            mime="text/csv",
        )
