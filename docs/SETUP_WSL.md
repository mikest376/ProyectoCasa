# Preparar WSL 2 y Docker

Guía para dejar el equipo listo. Cada cambio se enseña antes de aplicarlo, con copia de seguridad y forma de deshacerlo. Ningún script del repositorio modifica el sistema: los pasos de esta guía los ejecutas tú.

## 0. Diagnóstico

```bash
bash scripts/check_env.sh          # solo lee
bash scripts/check_env.sh --gpu    # además prueba la GPU en un contenedor
```

| Síntoma | Paso |
|---|---|
| `/etc/wsl.conf` tiene líneas que no son configuración, o trabajas como root | [1](#1-etcwslconf) |
| El proyecto pertenece a otro usuario, o `permission denied` al usar Docker | [2](#2-permisos) |
| RAM de WSL inferior a 21000 MiB | [3](#3-wslconfig) |
| Poco espacio, el daemon no responde o falla `docker pull` | [4](#4-docker-desktop) |

## 1. `/etc/wsl.conf`

El fichero contenía órdenes de shell pegadas por error (`echo -e "[user]..." | sudo tee`, `exit`). WSL las ignora, así que no hay usuario por defecto y la distribución entra como root.

**Ver y guardar una copia:**

```bash
WSL_USER=mati                    # tu usuario de Ubuntu
id "$WSL_USER"                   # debe existir y pertenecer al grupo sudo
cat -A /etc/wsl.conf
sudo cp -a /etc/wsl.conf "/etc/wsl.conf.bak.$(date +%F)"
```

**Preparar el contenido nuevo y compararlo, sin aplicarlo todavía:**

```bash
cat >/tmp/wsl.conf.nuevo <<EOF
[boot]
systemd=true

[user]
default=$WSL_USER
EOF
diff -u /etc/wsl.conf /tmp/wsl.conf.nuevo
```

**Aplicar, solo si la diferencia es la esperada:**

```bash
sudo install -m 644 /tmp/wsl.conf.nuevo /etc/wsl.conf
```

Se activa al reiniciar WSL ([paso 5](#5-reiniciar-y-verificar)). **Deshacer:** `sudo cp -a /etc/wsl.conf.bak.<fecha> /etc/wsl.conf`. Si algo impide entrar, desde PowerShell: `wsl -d Ubuntu -u root`.

## 2. Permisos

El usuario por defecto necesita pertenecer al grupo `docker`:

```bash
id -nG "$WSL_USER"                    # antes
sudo usermod -aG docker "$WSL_USER"
```

El grupo `docker` equivale a permisos de administrador dentro de la distribución: añade solo tu usuario. El cambio se aplica en la siguiente sesión.

**Propietario del proyecto.** Si está dentro de WSL (`/home/...`) y se creó como root, cámbialo después del paso 1:

```bash
ls -ld ~/Proyectos/ProyectoCasa       # antes
sudo chown -R "$WSL_USER:$WSL_USER" ~/Proyectos/ProyectoCasa
```

Si está en una unidad de Windows (`/mnt/d/...`), los permisos los gobierna NTFS y no hay que cambiar nada.

## 3. `.wslconfig`

Por defecto, WSL usa el 50 % de la RAM. En Windows, crea o edita `C:\Users\<usuario>\.wslconfig`:

```ini
[wsl2]
memory=22GB
processors=10
swap=4GB

[experimental]
autoMemoryReclaim=gradual
```

`autoMemoryReclaim=gradual` devuelve a Windows la caché de disco de Linux poco a poco ([documentación](https://learn.microsoft.com/en-us/windows/wsl/wsl-config); por defecto, `dropCache`). Sin ello, las compilaciones llenan la memoria y Windows amplía su fichero de paginación en `C:` ([TD-026](TECHNICAL_DEBT.md#td-026)).

Afecta a todas las distribuciones, incluida la de Docker Desktop. **Deshacer:** borra o renombra el fichero.

## 4. Docker Desktop

**Integración.** *Settings → Resources → WSL Integration* → activar Ubuntu.

**Espacio en disco.** Antes de descargar imágenes, mira el espacio libre de la unidad de **Windows** donde Docker guarda sus datos (por defecto, `C:`). `df` dentro de WSL engaña: su disco virtual anuncia 1 TB aunque el disco real esté lleno. El proyecto completo necesita unos 40 GB.

**Cambiar de unidad.** *Settings → Resources → Advanced → Disk image location* → *Apply & restart*. Docker mueve el disco y lo elimina de la unidad original. Hazlo antes de descargar imágenes: si la unidad se llena, el daemon se cae.

- Elige una carpeta vacía: Docker le añade `DockerDesktopWSL` al final. No copies `docker-data` a mano: quedan dos discos virtuales de unos 18 GB y Docker solo usa uno.
- Mientras copia, el motor aparece parado (`docker desktop status`). Desde un disco mecánico puede tardar más de una hora. No cierres Docker ni apagues el equipo.
- El ajuste no cambia hasta que la copia termina. Para comprobar la ubicación, desde PowerShell:

```powershell
(Get-Content "$env:APPDATA\Docker\settings-store.json" | ConvertFrom-Json).CustomWslDistroDir
```

**Ubuntu también ocupa un disco virtual**, que no se mueve con el de Docker. Con Docker Desktop cerrado:

```powershell
Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss | Get-ItemProperty | Select-Object DistributionName, BasePath
wsl --shutdown
wsl --manage Ubuntu --move E:\WSL\Ubuntu
```

**`docker-credential-desktop.exe` no encontrado.** La terminal o VS Code se abrieron antes de instalar Docker Desktop y tienen un PATH antiguo. Ciérralos y vuelve a abrirlos.

**GPU.** Controlador NVIDIA 570 o superior en Windows. Dentro de WSL no se instala ninguno.

## 5. Reiniciar y verificar

Cierra Docker Desktop y, desde PowerShell:

```powershell
wsl --shutdown
```

Abre Docker Desktop, espera a que indique *Engine running* y, en una terminal nueva de Ubuntu:

```bash
whoami                              # mati
free -h                             # unos 22 GiB
docker run --rm hello-world         # descarga y ejecución
bash scripts/check_env.sh --gpu     # 0 fallos
```
