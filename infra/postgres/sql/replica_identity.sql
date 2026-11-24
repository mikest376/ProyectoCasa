-- TD-005 · Evita que la publicación bloquee UPDATE y DELETE en Odoo.
--
-- PostgreSQL rechaza esas operaciones en tablas publicadas sin clave primaria ni
-- REPLICA IDENTITY. Asigna FULL a las que lo necesitan. Se puede repetir: solo
-- toca tablas pendientes. Hay que ejecutarlo tras instalar módulos de Odoo.

DO $$
DECLARE
    pendiente record;
BEGIN
    FOR pendiente IN
        SELECT c.oid::regclass AS tabla
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
          AND c.relpersistence = 'p'
          AND (
              c.relreplident = 'n'
              OR (
                  c.relreplident = 'd'
                  AND NOT EXISTS (
                      SELECT 1 FROM pg_index AS i
                      WHERE i.indrelid = c.oid AND i.indisprimary
                  )
              )
          )
        ORDER BY 1
    LOOP
        EXECUTE format('ALTER TABLE %s REPLICA IDENTITY FULL', pendiente.tabla);
        RAISE NOTICE 'REPLICA IDENTITY FULL: %', pendiente.tabla;
    END LOOP;
END
$$;
