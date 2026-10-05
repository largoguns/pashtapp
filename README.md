# PashtAPP · Finanzas Domésticas Saeta

PWA para las finanzas de casa que sustituye al libro de cálculo *Pashta Saeta*. Está hecha con FastAPI, SQLite (WAL), Jinja2, HTMX, Tailwind y Chart.js, y no usa Node.js en producción.

- **Móvil**: cabecera con los saldos *En Cuenta* y *Proyectado*, y un botón **+** que abre un bottom-sheet. Para apuntar un gasto basta con escribir el importe y tocar la categoría y Guardar. El movimiento puede quedar cobrado, pendiente, diferido 2 días, cargado el mes siguiente o fraccionado en cuotas. La conciliación se hace con un toque.
- **Escritorio**: KPIs del mes, tabla de fijos (con ingresos) frente a tabla de variables con buscador y filtros, donut por categoría, matriz anual de categorías, histórico de luz y fichas de préstamos con simulador de amortización anticipada.
- **Presupuestos por categoría** con semáforo (< 80 % verde, 80–100 % ámbar, > 100 % rojo).
- **Apertura automática de mes**: al visitar un mes por primera vez se generan los fijos de las plantillas y las cuotas de préstamo. Al conciliar el cargo, la cuota queda marcada como pagada.
- **API de ingesta rápida** para Atajos de iOS o SMS bancarios: `POST /api/v1/quick-expense`.
- **Exportación** a CSV por mes y copia completa en Excel. Hay copias de seguridad diarias comprimidas.

## Puesta en marcha (Docker / Portainer)

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"   # → SECRET_KEY
python scripts/hash_password.py                                  # → ADMIN_PASSWORD_HASH
docker compose up -d --build                                     # http://<servidor>:8080
```

> En `.env` el hash va entre **comillas simples** (`ADMIN_PASSWORD_HASH='$2b$12$…'`). Si lo pegas en el bloque `environment` de Portainer, escribe cada `$` como `$$`. `scripts/hash_password.py` imprime las dos variantes.

Pensado para la red local o Tailscale. Si lo sirves por HTTPS (p. ej. `tailscale serve`), pon `COOKIE_SECURE=true`.

### Migración desde el Excel

```bash
docker compose cp "Pashta Saeta 2026.xlsx" pashtapp-web:/tmp/import.xlsx
docker compose exec pashtapp-web python scripts/migrate_excel.py /tmp/import.xlsx --dry-run   # revisar
docker compose exec pashtapp-web python scripts/migrate_excel.py /tmp/import.xlsx            # importar
docker compose exec -u 0 pashtapp-web rm /tmp/import.xlsx
```

El migrador importa:

- las hojas Enero–Diciembre (salario, fijos, variables y sus casillas de cobrado);
- las categorías y la lista desplegable del libro;
- *Gastos Electricos*;
- los cuadros de *Coche*, *Grueso* y *Placas*, con detección de cuota final *balloon*;
- como saldo inicial, el «Resto mes anterior» de enero.

Además enlaza los cargos «Préstamo X» de cada mes con su cuota del cuadro y crea plantillas de fijos a partir del último mes. Al terminar compara el arrastre de saldo calculado con el «Resto mes anterior» de cada hoja. `--reset` vacía los datos antes de volver a importar.

> **Arrastre de saldo.** PashtAPP arrastra el saldo *efectivo* (sólo lo conciliado), como pide la especificación. El libro arrastra el saldo «tras cargos», que incluye también lo pendiente. Por eso coinciden en los meses cerrados y difieren en los meses que aún tienen cargos sin conciliar.

### Copias de seguridad

El contenedor `pashtapp_backup` hace a las 03:00 (Europe/Madrid) un `sqlite3 .backup` online, que es seguro en modo WAL. Lo guarda como `/backups/pashtapp_*.db.gz` y conserva 30 días. También se puede lanzar una copia desde *Ajustes → Datos y copias*.

Restaurar:

```bash
docker compose stop pashtapp-web
docker run --rm -v pashtapp_pashtapp_data:/data -v pashtapp_pashtapp_backups:/backups alpine \
  sh -c 'gunzip -c /backups/pashtapp_YYYYMMDD_HHMMSS.db.gz > /data/pashtapp.db && rm -f /data/pashtapp.db-wal /data/pashtapp.db-shm'
docker compose start pashtapp-web
```

El nombre exacto de los volúmenes lo da `docker volume ls`.

## API de ingesta rápida

Crea un token en *Ajustes → API / Atajos* o con `python scripts/create_api_key.py "iPhone"`.

```bash
# JSON estructurado
curl -X POST http://servidor:8080/api/v1/quick-expense -H "X-API-Key: pk_…" -H "Content-Type: application/json" \
  -d '{"amount": 14.50, "concept": "Mercadona", "category": "Comida", "is_settled": false}'
# Texto de SMS: se extraen el importe, el comercio y la categoría por palabras clave
curl -X POST http://servidor:8080/api/v1/quick-expense -H "X-API-Key: pk_…" -H "Content-Type: application/json" \
  -d '{"text": "Pago con tarjeta de 42,30 EUR en REPSOL..."}'
```

Otros endpoints:

- `GET /api/v1/summary?year=&month=`: saldos del mes.
- `POST /api/v1/loans/{id}/simulate-prepayment` con `{"prepayment_amount": 3000, "strategy": "REDUCE_TERM" | "REDUCE_FEE"}`: simulador de amortización anticipada.

Las palabras clave de categoría están en `app/services/parser.py`.

## Desarrollo

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ADMIN_USERNAME=admin ADMIN_PASSWORD=admin DATABASE_URL=sqlite:///./data/pashtapp.db uvicorn main:app --reload
pytest
scripts/build_css.sh          # recompila app/static/css/app.css (Tailwind standalone, sin Node)
```

Estructura:

- `app/models`: tablas SQLAlchemy.
- `app/services`: lógica de dominio (saldos, préstamos, apertura de mes, parser, exportación).
- `app/routers`: rutas.
- `app/templates`: Jinja2 y fragmentos HTMX.
- `app/static`: CSS compilado, JS, manifest, service worker e iconos.
- `scripts/`: migración, hash de contraseña, API keys, CSS e iconos.

El libro `.xlsx` y las bases de datos están en `.gitignore` porque contienen datos personales.
