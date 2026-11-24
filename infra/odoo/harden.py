# Se ejecuta con "odoo shell" después de instalar los módulos (scripts/init_odoo.sh).
# Cierra los accesos por defecto de una base con datos de demostración. Es repetible.
#
# Odoo crea admin/admin, demo/demo y portal/portal: con el ERP accesible desde fuera,
# cualquiera podría entrar.
import os
import secrets
import sys

password = os.environ.get("ODOO_ADMIN_PASSWORD", "")
if len(password) < 12 or password == "CHANGE_ME":
    sys.exit("ODOO_ADMIN_PASSWORD debe tener al menos 12 caracteres y no ser CHANGE_ME")

users = env["res.users"].with_context(active_test=False)  # noqa: F821 (env lo aporta odoo shell)
admin = env.ref("base.user_admin")  # noqa: F821
admin.password = password

closed = []
for login in ("demo", "portal"):
    user = users.search([("login", "=", login)], limit=1)
    if user:
        user.password = secrets.token_urlsafe(32)
        closed.append(login)

# Sin alta libre de usuarios ni recuperación de contraseña por correo.
params = env["ir.config_parameter"].sudo()  # noqa: F821
params.set_param("auth_signup.invitation_scope", "b2b")
params.set_param("auth_signup.reset_password", "False")

env.cr.commit()  # noqa: F821
print("HARDEN_OK admin actualizado; contraseñas aleatorias en:", ", ".join(closed) or "ninguno")
