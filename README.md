# Tablero de Seguimiento (Mural / Pianchu)

Tablero de tareas con **Streamlit** y **PostgreSQL (Supabase)**.

## Funciones
- Alta de tareas desde la barra lateral con **responsable de ejecución**, **responsable de validación (mail)** y **link al formulario de Google** donde se ejecuta la tarea.
- **Notificación por mail** al responsable de validación cuando se crea la tarea (desde la barra lateral o desde la tabla). Se puede reenviar desde el detalle de la tarea.
- **Colores por estado**: 🔴 Pendiente, 🟠 En Progreso, 🟡 En Revisión, 🟢 Completado.
- **Detalle de tarea** (botón 👁 en el Kanban) con acceso directo al formulario.
- **Tabla editable**: editar, agregar y borrar filas; solo se escriben las diferencias (INSERT/UPDATE/DELETE por `id`), en una transacción.
- **Kanban**: mover tareas entre estados con los botones ◀ ▶; marca las vencidas.
- **Filtros**: búsqueda, responsable de ejecución, de validación, prioridad, solo vencidas, ocultar completadas.
- **Resumen**: métricas y gráficos por estado y responsable.
- **Importar / Exportar**: plantilla Excel (con listas desplegables e instrucciones), exportación a Excel/CSV en el mismo formato e importación de Excel/CSV de otros tableros con vista previa, detección de errores por fila, omisión de duplicados y aviso por mail a los validadores. Reconoce encabezados y estados equivalentes (p. ej. «Responsable», «Vencimiento», «en curso», «cumplido»).
- Contraseña de acceso opcional.

## Puesta en marcha
```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml   # y completar
streamlit run app.py
```
`secrets.toml` necesita la URL de Supabase (`[connections.postgresql] url`) y, opcionalmente, `app_password`.
En Streamlit Cloud se pegan los mismos valores en *App settings → Secrets*.
También se acepta la variable de entorno `DATABASE_URL`.

### Notificaciones por mail (SMTP)
Agregá esto a los Secrets (con Gmail: activá la verificación en 2 pasos y creá una
*contraseña de aplicación* en https://myaccount.google.com/apppasswords):
```toml
app_url = "https://dashboard-mural-pianchu.streamlit.app"

[smtp]
host = "smtp.gmail.com"
port = 587
user = "tu-cuenta@gmail.com"
password = "xxxx xxxx xxxx xxxx"   # contraseña de aplicación, no la normal
from_name = "Tablero de Seguimiento"
```
Si falta `[smtp]`, la tarea se guarda igual y la app avisa que no se envió el mail.

> ⚠️ El repo es público: las contraseñas van **solo** en `.streamlit/secrets.toml` (ignorado por git) o en los Secrets de Streamlit Cloud, nunca en `secrets.toml.example`.

## Base de datos
La app crea la tabla `tasks` si no existe (ver `schema.sql`).
Si ya tenías datos de la versión anterior, la app lo detecta y muestra el aviso: ejecutá la sección **B) Migración** de `schema.sql` en el SQL Editor de Supabase (con backup previo).

## Tests
```bash
pip install -r requirements-dev.txt
pytest
```
