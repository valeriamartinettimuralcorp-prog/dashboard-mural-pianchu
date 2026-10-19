-- Esquema del Tablero de Seguimiento (PostgreSQL / Supabase)
-- La app crea la tabla sola si no existe. Este archivo sirve para crearla a mano
-- o para migrar una tabla creada con la versión anterior de la app.

-- ============ A) Instalación nueva ============
CREATE TABLE IF NOT EXISTS tasks (
    id             BIGSERIAL PRIMARY KEY,
    tarea          TEXT        NOT NULL,
    estado         TEXT        NOT NULL DEFAULT 'Pendiente',
    prioridad      TEXT        NOT NULL DEFAULT 'Media',
    responsable_ejecucion  TEXT NOT NULL DEFAULT '',
    responsable_validacion TEXT NOT NULL DEFAULT '',  -- mail del validador
    link_formulario        TEXT NOT NULL DEFAULT '',  -- link al formulario de Google
    fecha_limite   DATE,
    creado_en      TIMESTAMP   DEFAULT now(),
    actualizado_en TIMESTAMP   DEFAULT now()
);

-- Las columnas responsable_ejecucion / responsable_validacion / link_formulario
-- las agrega la app sola al iniciar (renombra `responsable` si existe).

-- ============ B) Migración de la tabla vieja ============
-- Ejecutar UNA vez (en el SQL Editor de Supabase) si la app avisa que la tabla
-- `tasks` tiene el esquema anterior. Hacer un backup/export antes.
--
-- BEGIN;
-- ALTER TABLE tasks ALTER COLUMN id SET NOT NULL;
-- DELETE FROM tasks WHERE id IS NULL;
-- CREATE SEQUENCE IF NOT EXISTS tasks_id_seq OWNED BY tasks.id;
-- SELECT setval('tasks_id_seq', COALESCE((SELECT MAX(id) FROM tasks), 0) + 1, false);
-- ALTER TABLE tasks ALTER COLUMN id SET DEFAULT nextval('tasks_id_seq');
-- ALTER TABLE tasks ADD PRIMARY KEY (id);
-- ALTER TABLE tasks ALTER COLUMN fecha_limite TYPE DATE USING NULLIF(fecha_limite::text, '')::date;
-- UPDATE tasks SET responsable = '' WHERE responsable IS NULL;
-- ALTER TABLE tasks ALTER COLUMN responsable SET DEFAULT '', ALTER COLUMN responsable SET NOT NULL;
-- ALTER TABLE tasks ADD COLUMN IF NOT EXISTS creado_en TIMESTAMP DEFAULT now();
-- ALTER TABLE tasks ADD COLUMN IF NOT EXISTS actualizado_en TIMESTAMP DEFAULT now();
-- COMMIT;
