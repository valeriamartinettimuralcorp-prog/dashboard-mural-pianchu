from datetime import date
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

# Configuración de la página
st.set_page_config(
    page_title="Tablero de Seguimiento", page_icon="📋", layout="wide"
)

st.title("📋 Tablero de Seguimiento (Supabase + Streamlit)")
st.markdown("Gestiona tus tareas en la nube con persistencia en tiempo real.")


# --- CONEXIÓN A SUPABASE (POSTGRESQL) ---
# Usamos st.connection de Streamlit para conectar a Postgres de forma limpia y segura
@st.cache_resource
def get_db_connection():
  # Lee la URL de conexión desde los secretos de Streamlit
  db_url = st.secrets["connections"]["postgresql"]["url"]
  engine = create_engine(db_url)
  return engine


try:
  engine = get_db_connection()
except Exception as e:
  st.error(
      "Error al conectar con la base de datos. Asegúrate de configurar los"
      " secretos en Streamlit Cloud."
  )
  st.stop()


# --- FUNCIONES DE DATOS ---
def load_data():
  with engine.connect() as conn:
    df = pd.read_sql("SELECT * FROM tasks ORDER BY id ASC", conn)
  return df


def save_task(tarea, estado, prioridad, responsable, fecha_limite):
  with engine.begin() as conn:
    query = text("""
            INSERT INTO tasks (tarea, estado, prioridad, responsable, fecha_limite)
            VALUES (:tarea, :estado, :prioridad, :responsable, :fecha_limite)
        """)
    conn.execute(
        query,
        {
            "tarea": tarea,
            "estado": estado,
            "prioridad": prioridad,
            "responsable": responsable,
            "fecha_limite": str(fecha_limite),
        },
    )


def update_task_from_df(df_updated):
  # Sobrescribimos la tabla con los cambios de la grilla
  with engine.begin() as conn:
    # Primero vaciamos o actualizamos, para simplificar usamos to_sql con replace
    df_updated.to_sql("tasks", conn, if_exists="replace", index=False)


# --- BARRA LATERAL PARA NUEVAS TAREAS ---
st.sidebar.header("➕ Nueva Tarea")
with st.sidebar.form("new_task_form", clear_on_submit=True):
  nueva_tarea = st.text_input("Nombre de la tarea")
  estado_nuevo = st.selectbox(
      "Estado", ["Pendiente", "En Progreso", "En Revisión", "Completado"]
  )
  prioridad_nueva = st.selectbox("Prioridad", ["Baja", "Media", "Alta", "Urgente"])
  responsable_nuevo = st.text_input("Responsable")
  fecha_nueva = st.date_input("Fecha límite", value=date.today())

  submitted = st.form_submit_button("Agregar Tarea")
  if submitted and nueva_tarea:
    save_task(
        nueva_tarea,
        estado_nuevo,
        prioridad_nueva,
        responsable_nuevo,
        fecha_nueva,
    )
    st.sidebar.success("¡Tarea agregada con éxito!")
    st.rerun()

# --- CARGAR DATOS ACTUALES ---
df = load_data()

# --- INTERFAZ PRINCIPAL (PESTAÑAS) ---
tab1, tab2, tab3 = st.tabs(
    ["📊 Vista Tabla Editable", "📌 Vista por Estados", "⚙️ Resumen"]
)

with tab1:
  st.subheader("Grilla de Tareas (Edición directa)")
  if df.empty:
    st.info("No hay tareas registradas. Usa la barra lateral para agregar una.")
  else:
    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="task_editor",
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True),
            "estado": st.column_config.SelectboxColumn(
                "Estado",
                options=[
                    "Pendiente",
                    "En Progreso",
                    "En Revisión",
                    "Completado",
                ],
                required=True,
            ),
            "prioridad": st.column_config.SelectboxColumn(
                "Prioridad",
                options=["Baja", "Media", "Alta", "Urgente"],
                required=True,
            ),
        },
    )

    if st.button("Guardar Cambios de la Tabla"):
      try:
        update_task_from_df(edited_df)
        st.success("¡Cambios guardados correctamente en Supabase!")
        st.rerun()
      except Exception as ex:
        st.error(f"Error al guardar: {ex}")

with tab2:
  st.subheader("Tablero Kanban (Por Estado)")
  if df.empty:
    st.info("No hay tareas para mostrar en el tablero.")
  else:
    estados = ["Pendiente", "En Progreso", "En Revisión", "Completado"]
    cols = st.columns(len(estados))

    for idx, estado in enumerate(estados):
      with cols[idx]:
        st.markdown(f"### {estado}")
        tareas_estado = df[df["estado"] == estado]
        for _, row in tareas_estado.iterrows():
          with st.container(border=True):
            st.markdown(f"**{row['tarea']}**")
            st.caption(
                f"👤 {row['responsable'] or 'Sin asignar'} | ⚡"
                f" {row['prioridad']}"
            )
            st.text(f"📅 {row['fecha_limite']}")

with tab3:
  st.subheader("Estadísticas y Exportación")
  if not df.empty:
    col1, col2, col3 = st.columns(3)
    col1.metric("Total de Tareas", len(df))
    col2.metric("Completadas", len(df[df["estado"] == "Completado"]))
    col3.metric(
        "Pendientes / En Proceso",
        len(df[df["estado"].isin(["Pendiente", "En Progreso"])]),
    )

    st.markdown("---")
    st.download_button(
        label="Descargar CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="tareas_seguimiento.csv",
        mime="text/csv",
    )