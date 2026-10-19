import hmac
import os
from datetime import date
from html import escape

import pandas as pd
import streamlit as st

import db
import io_tareas
import notify

st.set_page_config(page_title="Tablero de Seguimiento", page_icon="📋", layout="wide")

# Colores por estado: (borde/acento, fondo translúcido, emoji)
COLORES = {
    "Pendiente": ("#d93025", "rgba(217, 48, 37, 0.14)", "🔴"),
    "En Progreso": ("#f57c00", "rgba(245, 124, 0, 0.16)", "🟠"),
    "En Revisión": ("#f9c400", "rgba(249, 196, 0, 0.20)", "🟡"),
    "Completado": ("#2e7d32", "rgba(46, 125, 50, 0.16)", "🟢"),
}
SLUG = {"Pendiente": "pendiente", "En Progreso": "progreso", "En Revisión": "revision", "Completado": "completado"}


def inject_css():
    reglas = []
    for estado, (borde, fondo, _) in COLORES.items():
        s = SLUG[estado]
        reglas.append(
            f'div[class*="st-key-card-{s}-"] {{ background: {fondo}; '
            f"border: 1px solid {borde} !important; border-left: 8px solid {borde} !important; "
            "border-radius: 10px; padding: 10px 12px; }"
        )
    reglas.append(
        ".estado-head { color: #fff; padding: 6px 12px; border-radius: 8px; "
        "font-weight: 700; text-align: center; margin-bottom: 8px; }"
    )
    reglas.append(
        ".estado-pill { color: #fff; padding: 2px 10px; border-radius: 999px; "
        "font-weight: 600; font-size: 0.85rem; }"
    )
    st.markdown(f"<style>{''.join(reglas)}</style>", unsafe_allow_html=True)


def pill(estado, cls="estado-pill", extra=""):
    borde = COLORES.get(estado, ("#777",))[0]
    txt_color = "#222" if estado == "En Revisión" else "#fff"
    return (
        f"<div class='{cls}' style='background:{borde};color:{txt_color};display:inline-block'>"
        f"{escape(estado)}{extra}</div>"
    )


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
    # La conexión queda en caché entre despliegues: si el código nuevo trae
    # columnas nuevas, se agregan acá sin tener que reiniciar la app.
    try:
        db.ensure_schema(engine)
        problems = db.schema_problems(engine)
    except Exception as ex:
        problems.append(f"No se pudo actualizar la tabla automáticamente: {ex}")
if problems:
    st.error("La tabla `tasks` tiene el esquema de la versión anterior y hay que migrarla:")
    for p in problems:
        st.markdown(f"- {p}")
    st.markdown(
        "Ejecutá la sección **B) Migración** de `schema.sql` en el SQL Editor de "
        "Supabase (hacé un backup antes) y recargá la página."
    )
    st.stop()

inject_css()

SMTP = dict(secret(["smtp"], {}) or {})
APP_URL = secret(["app_url"], "") or ""


def flash(kind, msg):
    st.session_state.setdefault("flash", []).append((kind, msg))


def run(action, ok_msg=None):
    """Ejecuta una acción de escritura mostrando errores sin tirar la app."""
    try:
        result = action()
    except Exception as ex:
        st.error(f"Error: {ex}")
        return None
    if ok_msg:
        flash("success", ok_msg(result) if callable(ok_msg) else ok_msg)
    return result if result is not None else True


def notificar(tareas):
    """Avisa por mail al responsable de validación de cada tarea nueva."""
    con_mail = [t for t in tareas if t.get("responsable_validacion")]
    if not con_mail:
        return
    if not notify.smtp_configured(SMTP):
        flash(
            "warning",
            "No se envió la notificación por mail: falta configurar la sección "
            "`[smtp]` en los Secrets (ver README).",
        )
        return
    for t in con_mail:
        try:
            notify.send_validation_assignment(SMTP, t, APP_URL)
            flash("info", f"📧 Notificación enviada a {t['responsable_validacion']}.")
        except Exception as ex:
            flash("warning", f"No se pudo enviar el mail a {t['responsable_validacion']}: {ex}")


# --------------------------------------------------------------------------
# Detalle de tarea
# --------------------------------------------------------------------------
@st.dialog("Detalle de la tarea", width="large")
def ver_tarea(t: dict):
    st.markdown(f"### {escape(t['tarea'])}")
    st.markdown(pill(t["estado"]), unsafe_allow_html=True)
    fecha = t.get("fecha_limite")
    c1, c2 = st.columns(2)
    c1.markdown(f"**👤 Responsable de ejecución**  \n{t.get('responsable_ejecucion') or 'Sin asignar'}")
    c2.markdown(f"**✅ Responsable de validación**  \n{t.get('responsable_validacion') or 'Sin asignar'}")
    c1.markdown(f"**⚡ Prioridad**  \n{t.get('prioridad')}")
    c2.markdown(
        f"**📅 Fecha límite**  \n{fecha.strftime('%d/%m/%Y') if fecha and not pd.isna(fecha) else 'Sin fecha'}"
        + ("  🔴 **Vencida**" if t.get("vencida") else "")
    )
    st.markdown("---")
    if t.get("link_formulario"):
        st.link_button("📝 Abrir formulario de la tarea", t["link_formulario"], type="primary", width="stretch")
    else:
        st.caption("Esta tarea no tiene formulario cargado. Podés agregarlo desde la tabla editable.")
    if t.get("responsable_validacion"):
        if st.button("📧 Reenviar notificación al validador", width="stretch"):
            notificar([t])
            st.rerun()


# --------------------------------------------------------------------------
# Encabezado
# --------------------------------------------------------------------------
head_l, head_r = st.columns([6, 1])
head_l.title("📋 Tablero de Seguimiento")
if head_r.button("🔄 Recargar", width="stretch"):
    st.rerun()
for kind, msg in st.session_state.pop("flash", []):
    getattr(st, kind)(msg)

# --------------------------------------------------------------------------
# Nueva tarea
# --------------------------------------------------------------------------
st.sidebar.header("➕ Nueva Tarea")
with st.sidebar.form("new_task_form", clear_on_submit=True):
    nueva_tarea = st.text_input("Nombre de la tarea")
    estado_nuevo = st.selectbox("Estado", db.ESTADOS)
    prioridad_nueva = st.selectbox("Prioridad", db.PRIORIDADES, index=1)
    resp_ejec_nuevo = st.text_input("Responsable de ejecución")
    resp_valid_nuevo = st.text_input(
        "Responsable de validación (mail)",
        placeholder="nombre@empresa.com",
        help="Se le envía un mail avisándole que tiene que validar esta tarea.",
    )
    link_nuevo = st.text_input(
        "Link del formulario de Google",
        placeholder="https://forms.gle/...",
        help="Formulario donde se ejecuta la tarea.",
    )
    fecha_nueva = st.date_input("Fecha límite", value=date.today(), format="DD/MM/YYYY")
    submitted = st.form_submit_button("Agregar Tarea", type="primary")

if submitted:
    mail = resp_valid_nuevo.strip()
    if not nueva_tarea.strip():
        st.sidebar.error("Poné un nombre para la tarea.")
    elif mail and not db.EMAIL_RE.match(mail):
        st.sidebar.error("El responsable de validación tiene que ser un mail válido.")
    else:
        nueva = run(
            lambda: db.add_task(
                engine,
                nueva_tarea,
                estado_nuevo,
                prioridad_nueva,
                resp_ejec_nuevo,
                fecha_nueva,
                responsable_validacion=mail,
                link_formulario=link_nuevo,
            ),
            "¡Tarea agregada!",
        )
        if nueva:
            notificar([nueva])
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
f_ejec = st.sidebar.multiselect("Responsable de ejecución", sorted({r for r in df["responsable_ejecucion"] if r}))
f_valid = st.sidebar.multiselect("Responsable de validación", sorted({r for r in df["responsable_validacion"] if r}))
f_prio = st.sidebar.multiselect("Prioridad", db.PRIORIDADES)
solo_vencidas = st.sidebar.checkbox("Solo vencidas")
ocultar_completadas = st.sidebar.checkbox("Ocultar completadas")

fdf = df
if buscar:
    fdf = fdf[fdf["tarea"].str.contains(buscar, case=False, na=False)]
if f_ejec:
    fdf = fdf[fdf["responsable_ejecucion"].isin(f_ejec)]
if f_valid:
    fdf = fdf[fdf["responsable_validacion"].isin(f_valid)]
if f_prio:
    fdf = fdf[fdf["prioridad"].isin(f_prio)]
if solo_vencidas:
    fdf = fdf[fdf["vencida"]]
if ocultar_completadas:
    fdf = fdf[fdf["estado"] != "Completado"]
if len(fdf) != len(df):
    st.caption(f"Mostrando {len(fdf)} de {len(df)} tareas (filtros activos).")

tab_kanban, tab_tabla, tab_resumen, tab_io = st.tabs(
    ["📌 Tablero Kanban", "📊 Tabla Editable", "⚙️ Resumen", "📥 Importar / Exportar"]
)

# --------------------------------------------------------------------------
# Kanban
# --------------------------------------------------------------------------
with tab_kanban:
    if fdf.empty:
        st.info("No hay tareas para mostrar.")
    else:
        cols = st.columns(len(db.ESTADOS))
        for idx, estado in enumerate(db.ESTADOS):
            sub = fdf[fdf["estado"] == estado]
            with cols[idx]:
                st.markdown(
                    pill(estado, "estado-head", f" ({len(sub)})").replace("display:inline-block", "display:block"),
                    unsafe_allow_html=True,
                )
                for _, row in sub.iterrows():
                    tid = int(row["id"])
                    with st.container(key=f"card-{SLUG[estado]}-{tid}"):
                        st.markdown(f"**{row['tarea']}**")
                        st.caption(f"👤 Ejecuta: {row['responsable_ejecucion'] or 'Sin asignar'}")
                        st.caption(f"✅ Valida: {row['responsable_validacion'] or 'Sin asignar'}")
                        linea = f"⚡ {row['prioridad']}"
                        if pd.notna(row["fecha_limite"]) and row["fecha_limite"] is not None:
                            fecha = row["fecha_limite"].strftime("%d/%m/%Y")
                            linea += f" · {'🔴 Vencida ' if row['vencida'] else '📅 '}{fecha}"
                        st.caption(linea)
                        if row["link_formulario"]:
                            st.link_button("📝 Formulario", row["link_formulario"], width="stretch")
                        b1, b2, b3 = st.columns(3)
                        if idx > 0 and b1.button("◀", key=f"prev_{tid}", help=f"Mover a {db.ESTADOS[idx-1]}"):
                            if run(lambda: db.set_estado(engine, tid, db.ESTADOS[idx - 1])):
                                st.rerun()
                        if b2.button("👁", key=f"ver_{tid}", help="Ver tarea"):
                            ver_tarea(row.to_dict())
                        if idx < len(db.ESTADOS) - 1 and b3.button("▶", key=f"next_{tid}", help=f"Mover a {db.ESTADOS[idx+1]}"):
                            if run(lambda: db.set_estado(engine, tid, db.ESTADOS[idx + 1])):
                                st.rerun()

# --------------------------------------------------------------------------
# Tabla editable
# --------------------------------------------------------------------------
with tab_tabla:
    st.caption(
        "Editá celdas, agregá filas al final o borrá filas seleccionadas; "
        "después tocá **Guardar cambios**. Las tareas nuevas con mail de validación "
        "envían la notificación al guardar."
    )
    base = fdf[db.COLUMNAS].reset_index(drop=True)
    vista = base.copy()
    vista.insert(1, "color", vista["estado"].map(lambda e: COLORES.get(e, ("", "", ""))[2]))
    edited = st.data_editor(
        vista,
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        key="task_editor",
        column_order=["color", "id", "tarea", "estado", "prioridad", "responsable_ejecucion",
                      "responsable_validacion", "fecha_limite", "link_formulario"],
        column_config={
            "color": st.column_config.TextColumn("", disabled=True, width="small"),
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "tarea": st.column_config.TextColumn("Tarea", required=True),
            "estado": st.column_config.SelectboxColumn(
                "Estado", options=db.ESTADOS, required=True, default="Pendiente"
            ),
            "prioridad": st.column_config.SelectboxColumn(
                "Prioridad", options=db.PRIORIDADES, required=True, default="Media"
            ),
            "responsable_ejecucion": st.column_config.TextColumn("Resp. ejecución"),
            "responsable_validacion": st.column_config.TextColumn(
                "Resp. validación (mail)", validate=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$"
            ),
            "fecha_limite": st.column_config.DateColumn("Fecha límite", format="DD/MM/YYYY"),
            "link_formulario": st.column_config.LinkColumn(
                "Formulario", help="Link del formulario de Google donde se ejecuta la tarea"
            ),
        },
    )
    if st.button("💾 Guardar cambios", type="primary"):
        res = run(
            lambda: db.apply_changes(engine, base, edited.drop(columns=["color"])),
            lambda r: (
                f"Guardado: {r['insertadas']} nuevas, {r['modificadas']} modificadas, "
                f"{r['eliminadas']} eliminadas."
            ),
        )
        if res:
            notificar(res["nuevas"])
            st.rerun()

# --------------------------------------------------------------------------
# Resumen
# --------------------------------------------------------------------------
with tab_resumen:
    if df.empty:
        st.info("Todavía no hay tareas.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", len(df))
        c2.metric("Completadas", int((df["estado"] == "Completado").sum()))
        c3.metric("Pendientes / En progreso", int(df["estado"].isin(["Pendiente", "En Progreso"]).sum()))
        c4.metric("Vencidas", int(df["vencida"].sum()))

        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Tareas por estado**")
            conteo = df["estado"].value_counts().reindex(db.ESTADOS, fill_value=0)
            try:
                import altair as alt

                datos = conteo.rename_axis("estado").reset_index(name="tareas")
                chart = (
                    alt.Chart(datos)
                    .mark_bar()
                    .encode(
                        x=alt.X("estado:N", sort=db.ESTADOS, title=None),
                        y=alt.Y("tareas:Q", title="Tareas"),
                        color=alt.Color(
                            "estado:N",
                            scale=alt.Scale(domain=db.ESTADOS, range=[COLORES[e][0] for e in db.ESTADOS]),
                            legend=None,
                        ),
                    )
                )
                st.altair_chart(chart, width="stretch")
            except ImportError:
                st.bar_chart(conteo)
        with g2:
            st.markdown("**Tareas abiertas por responsable de ejecución**")
            abiertas = df[df["estado"] != "Completado"]
            por_resp = abiertas["responsable_ejecucion"].replace("", "Sin asignar").value_counts()
            if por_resp.empty:
                st.caption("No hay tareas abiertas.")
            else:
                st.bar_chart(por_resp)


# --------------------------------------------------------------------------
# Importar / Exportar
# --------------------------------------------------------------------------
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

with tab_io:
    st.subheader("1. Plantilla y exportación")
    st.caption(
        "La plantilla y la exportación usan el mismo formato: un archivo exportado "
        "se puede editar y volver a importar."
    )
    e1, e2, e3 = st.columns(3)
    e1.download_button(
        "📄 Descargar plantilla vacía (Excel)",
        data=io_tareas.plantilla_xlsx(),
        file_name="plantilla_tareas.xlsx",
        mime=XLSX,
        width="stretch",
    )
    e2.download_button(
        f"⬇️ Exportar {len(fdf)} tareas (Excel)",
        data=io_tareas.exportar_xlsx(fdf),
        file_name=f"tareas_{hoy:%Y%m%d}.xlsx",
        mime=XLSX,
        width="stretch",
        disabled=fdf.empty,
        help="Exporta las tareas visibles (respeta los filtros de la barra lateral).",
    )
    e3.download_button(
        f"⬇️ Exportar {len(fdf)} tareas (CSV)",
        data=io_tareas.exportar_csv(fdf),
        file_name=f"tareas_{hoy:%Y%m%d}.csv",
        mime="text/csv",
        width="stretch",
        disabled=fdf.empty,
    )

    st.markdown("---")
    st.subheader("2. Importar tareas")
    st.caption(
        "Subí la plantilla completa o un Excel/CSV de otro tablero. Se reconocen encabezados "
        "parecidos (p. ej. «Responsable», «Vencimiento», «Status») y estados como «en curso», "
        "«a validar» o «cumplido». Nada se guarda hasta que confirmes."
    )
    up_key = f"import_file_{st.session_state.get('import_n', 0)}"
    archivo = st.file_uploader("Archivo (.xlsx o .csv)", type=["xlsx", "xlsm", "csv"], key=up_key)

    if archivo is not None:
        try:
            prep = io_tareas.preparar(io_tareas.leer_archivo(archivo.name, archivo.getvalue()))
        except Exception as ex:
            st.error(f"No se pudo leer el archivo: {ex}")
            prep = None

        if prep:
            validas, errores = prep["validas"], prep["errores"]
            reconocidas = ", ".join(
                f"«{col}» → {io_tareas.ENCABEZADOS[c]}" for col, c in prep["mapa"].items()
            )
            st.caption(f"Columnas reconocidas: {reconocidas}")
            if prep["ignoradas"]:
                st.caption(f"Columnas ignoradas: {', '.join(prep['ignoradas'])}")

            existentes = {t.strip().lower() for t in df["tarea"]}
            hay_ids = any(v.get("id") is not None for v in validas)

            o1, o2, o3 = st.columns(3)
            actualizar = o1.checkbox(
                "Actualizar las que tengan ID existente",
                value=False,
                disabled=not hay_ids,
                help="Usalo solo con archivos exportados de ESTE tablero. "
                "Si no, todas se cargan como tareas nuevas.",
            )
            omitir_dup = o2.checkbox(
                "Omitir tareas con nombre ya existente",
                value=True,
                help="Evita duplicar tareas si importás dos veces el mismo archivo.",
            )
            avisar = o3.checkbox("Enviar mail a los validadores de las nuevas", value=True)

            ids_db = set(df["id"].astype(int))
            a_cargar, duplicadas = [], 0
            for v in validas:
                es_update = actualizar and v.get("id") in ids_db
                if not es_update and omitir_dup and v["tarea"].strip().lower() in existentes:
                    duplicadas += 1
                    continue
                a_cargar.append(v)

            m1, m2, m3 = st.columns(3)
            m1.metric("Listas para importar", len(a_cargar))
            m2.metric("Con errores", len(errores))
            m3.metric("Omitidas por duplicadas", duplicadas)

            if errores:
                st.warning("Estas filas no se van a importar. Corregilas en el archivo y volvé a subirlo:")
                st.dataframe(pd.DataFrame(errores), hide_index=True, width="stretch")

            if a_cargar:
                vista_imp = pd.DataFrame(a_cargar)
                vista_imp.insert(0, "color", vista_imp["estado"].map(lambda e: COLORES[e][2]))
                st.dataframe(
                    vista_imp[["color", "_fila", "tarea", "estado", "prioridad", "responsable_ejecucion",
                               "responsable_validacion", "fecha_limite", "link_formulario"]],
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "color": st.column_config.TextColumn("", width="small"),
                        "_fila": st.column_config.NumberColumn("Fila", width="small"),
                        "tarea": "Tarea",
                        "estado": "Estado",
                        "prioridad": "Prioridad",
                        "responsable_ejecucion": "Resp. ejecución",
                        "responsable_validacion": "Resp. validación",
                        "fecha_limite": st.column_config.DateColumn("Fecha límite", format="DD/MM/YYYY"),
                        "link_formulario": st.column_config.LinkColumn("Formulario"),
                    },
                )
                if st.button(f"📥 Importar {len(a_cargar)} tareas", type="primary"):
                    res = run(
                        lambda: db.import_tasks(engine, a_cargar, actualizar_por_id=actualizar),
                        lambda r: f"Importación lista: {r['insertadas']} nuevas, {r['actualizadas']} actualizadas.",
                    )
                    if res:
                        if avisar:
                            notificar(res["nuevas"])
                        st.session_state["import_n"] = st.session_state.get("import_n", 0) + 1
                        st.rerun()
            elif not errores:
                st.info("No hay tareas nuevas para importar en este archivo.")
