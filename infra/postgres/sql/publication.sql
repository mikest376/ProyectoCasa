-- Publicación para la captura global del esquema public (incluye tablas futuras).
-- Requiere superusuario. Ejecutar siempre después de replica_identity.sql.

\getenv publication DEBEZIUM_PUBLICATION_NAME

SELECT format('CREATE PUBLICATION %I FOR TABLES IN SCHEMA public', :'publication')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_publication WHERE pubname = :'publication'
)
\gexec
