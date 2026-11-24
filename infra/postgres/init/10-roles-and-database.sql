-- Se ejecuta una sola vez, al crear el volumen, como superusuario.
--
--   Odoo      propietario de su base, SIN superusuario: un fallo en el ERP no da
--             acceso a COPY ... TO PROGRAM ni a otras bases.
--   Debezium  replicación y lectura, sin escritura (CLAUDE.md, regla 2).
--
-- Las contraseñas llegan por variables de entorno: no aparecen en argumentos.

\getenv odoo_db ODOO_DB_NAME
\getenv odoo_user ODOO_DB_USER
\getenv odoo_password ODOO_DB_PASSWORD
\getenv dbz_user DEBEZIUM_DB_USER
\getenv dbz_password DEBEZIUM_DB_PASSWORD

CREATE ROLE :"odoo_user" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    PASSWORD :'odoo_password';

CREATE ROLE :"dbz_user" LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE
    CONNECTION LIMIT 8 PASSWORD :'dbz_password';

-- Misma ordenación que usa Odoo al crear bases de datos.
CREATE DATABASE :"odoo_db" OWNER :"odoo_user"
    TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'en_US.utf8';

REVOKE CONNECT ON DATABASE :"odoo_db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"odoo_db" TO :"dbz_user";

\connect :"odoo_db"

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

GRANT USAGE ON SCHEMA public TO :"dbz_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"odoo_user" IN SCHEMA public
    GRANT SELECT ON TABLES TO :"dbz_user";
