# AGENT SPEC: PashtAPP (Migración de "Pashta Saeta" a Web PWA)

Este documento constituye la especificación funcional y técnica exhaustiva para implementar de manera autónoma **PashtAPP**, una aplicación web progresiva (PWA) responsiva para finanzas personales y domésticas, orientada a sustitución completa del libro de cálculo multianual *Pashta Saeta*.

## 1. Identidad del Proyecto y Metadatos

* **Nombre Oficial**: PashtAPP
* **Subtítulo / Contexto**: Finanzas Domésticas Saeta
* **Short Name (PWA)**: PashtAPP
* **Repositorio Remoto Oficial**: `https://github.com/largoguns/pashtapp.git`
* **Directorio raíz del proyecto**: `pashtapp/`
* **Contenedores Docker**: `pashtapp_web` y `pashtapp_backup`
* **Base de Datos**: `/data/pashtapp.db`

## 2. Directivas de Ejecución para el Agente

1. **Stack de Ejecución**: Python 3.12 (FastAPI) + SQLite (modo WAL con SQLAlchemy 2.0 / SQLModel) + Jinja2 + HTMX + Tailwind CSS (standalone / CDN) + Chart.js.
2. **Filosofía**: Zero-bloat, sin runtime de Node.js en producción, contenedor Docker monolítico ultraligero (<150 MB de RAM en reposo).
3. **Persistencia y Respaldo**: SQLite montado en `/data/pashtapp.db` con copias de seguridad automatizadas en `/backups/pashtapp_*.db.gz`.
4. **Seguridad**: Autenticación de sesión HTTP/cookie cifrada con contraseña hasheada (bcrypt). Diseñada para operar de forma segura en red local o a través de VPN (Tailscale).
5. **Responsividad Estricta**:
   * **Móvil (< 768px)**: Enfoque nativo con Bottom-Sheet para entrada ultrarrápida de gastos en 3 toques, toggle táctil de conciliación de cargos bancarios y consulta instantánea de saldos.
   * **Escritorio (>= 768px)**: Tablero analítico completo a dos columnas (Fijos vs. Variables), matriz comparativa anual de categorías, histórico de luz y panel de préstamos.
6. **Convención Git**: El agente debe estructurar los commits de forma semántica (`feat:`, `fix:`, `refactor:`, `docs:`) listos para sincronizar con `origin/main` en `https://github.com/largoguns/pashtapp.git`.

## 3. Modelo de Datos Relacional (SQLite / SQLAlchemy)

```sql
-- 1. Usuarios para autenticación básica
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. Categorías y Límites Presupuestarios (Mejora 4)
CREATE TABLE categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    icon TEXT DEFAULT 'tag',
    color_hex TEXT DEFAULT '#64748b',
    is_fixed_default BOOLEAN DEFAULT FALSE,
    monthly_budget_limit REAL DEFAULT NULL -- Límite presupuestario mensual opcional
);

-- 3. Plantillas de Gastos e Ingresos Recurrentes (Fijos)
CREATE TABLE recurring_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    default_amount REAL NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    day_of_month INTEGER DEFAULT 1,
    is_income BOOLEAN DEFAULT FALSE,
    active BOOLEAN DEFAULT TRUE
);

-- 4. Movimientos / Transacciones
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,                        -- Fecha de operación
    settlement_date DATE,                     -- Fecha real de cobro bancario
    name TEXT NOT NULL,
    amount REAL NOT NULL,                     -- Positivo para ingresos, negativo para gastos
    is_income BOOLEAN DEFAULT FALSE,
    category_id INTEGER REFERENCES categories(id),
    is_fixed BOOLEAN DEFAULT FALSE,
    is_settled BOOLEAN DEFAULT FALSE,         -- Checkbox de "Efectivo / Cobrado"
    installment_group_id TEXT DEFAULT NULL,   -- UUID para compras fraccionadas
    installment_number INTEGER DEFAULT NULL,  -- Cuota N
    installment_total INTEGER DEFAULT NULL,   -- de M cuotas
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 5. Préstamos Activos (Amortización Francesa: Coche, Grueso, Placas)
CREATE TABLE loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                       -- Ej: "Coche", "Grueso", "Placas"
    initial_capital REAL NOT NULL,
    annual_interest_rate REAL NOT NULL,       -- Porcentaje (ej: 4.5 para 4.5%)
    term_months INTEGER NOT NULL,
    start_date DATE NOT NULL,
    monthly_fee REAL NOT NULL,
    active BOOLEAN DEFAULT TRUE
);

-- 6. Cuadro de Amortización por Préstamo
CREATE TABLE loan_installments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id INTEGER REFERENCES loans(id) ON DELETE CASCADE,
    installment_number INTEGER NOT NULL,
    due_date DATE NOT NULL,
    payment_amount REAL NOT NULL,
    capital_amount REAL NOT NULL,
    interest_amount REAL NOT NULL,
    remaining_capital REAL NOT NULL,
    is_paid BOOLEAN DEFAULT FALSE
);

-- 7. Histórico de Electricidad
CREATE TABLE utility_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    amount REAL NOT NULL,
    kwh REAL DEFAULT NULL,
    notes TEXT,
    UNIQUE(year, month)
);

-- 8. Webhooks / Token para parseo automático de SMS o Atajos (Mejora 2)
CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## 4. Lógica de Negocio y Reglas de Dominio

### 4.1. Cálculo de Saldos Mensuales

Para cualquier mes seleccionado ($M$):

1. **Saldo Inicial**: Arrastre del saldo de cierre efectivo del mes $M-1$ o saldo inicial configurado.
2. **Ingresos Previstos**: Suma de ingresos configurados en el mes.
3. **Saldo Actual en Cuenta (Efectivo)**:
   $$\text{Saldo Actual} = \text{Saldo Inicial} + \sum(\text{Ingresos Cobrados}) - \sum(\text{Gastos con } is\_settled = \text{TRUE})$$
4. **Saldo Proyectado a Fin de Mes**:
   $$\text{Saldo Proyectado} = \text{Saldo Actual} - \sum(\text{Gastos con } is\_settled = \text{FALSE})$$

### 4.2. Gestión de Tarjeta Diferida y Fraccionada

* **Gasto Inmediato**: `settlement_date = date`, `is_settled` configurable manualmente.
* **Diferido 2 Días**: `settlement_date = date + 2 días`, `is_settled = FALSE`.
* **Diferido al Mes Siguiente**: `settlement_date = primer día del mes siguiente`.
* **Fraccionado en** $N$ **Meses**:
  * Al registrar un importe $T$ fraccionado en $N$ cuotas:
  * Generar $N$ transacciones vinculadas por `installment_group_id = UUID`.
  * Cada una con importe $T / N$, fecha correspondiente al mes $+i$, y nota `[Cuota i/N]`.

### 4.3. Transición de Mes (Apertura Automática)

* PashtAPP no requiere duplicar archivos ni hojas:
  * Al cambiar de mes o invocar la apertura de un nuevo mes, se evalúan las reglas activas de `recurring_templates`.
  * Se insertan las transacciones fijas correspondientes si aún no existen.
  * Se reconcilian las cuotas de préstamos programadas para ese mes.

## 5. Implementación de las 5 Mejoras Específicas

### Mejora 1: Script de Migración Inicial desde Excel

* **Ruta**: `scripts/migrate_excel.py`.
* **Funcionalidad**:
  1. Utiliza `openpyxl` para analizar el archivo `.xlsx` (hojas mensuales de enero a diciembre, hoja consolidada de categorías, hoja de electricidad y las tres hojas de amortización: "Coche", "Grueso", "Placas").
  2. Mapea categorías existentes y puebla la tabla `categories`.
  3. Mapea gastos fijos y variables a `transactions`, transformando las casillas de verificación en `is_settled = TRUE`.
  4. Extrae los cuadros de amortización hacia `loans` y `loan_installments`.
  5. Vuelca el histórico de electricidad a `utility_readings`.

### Mejora 2: Endpoint de Ingesta Rápida (SMS / Notificaciones / Atajos iOS)

* **Ruta**: `POST /api/v1/quick-expense`
* **Cabecera**: `X-API-Key: <token>`
* **Entrada**:
  * **JSON Estructurado**: `{"amount": 14.50, "concept": "Mercadona", "category": "Comida", "is_settled": false}`
  * **Texto Plano (SMS Bancario)**: `{"text": "Pago con tarjeta de 42,30 EUR en REPSOL..."}`
* **Lógica del Parser**:
  * Regex para captura de importe: `r'(?i)(?:importe|pago|compra|por)?\s*([0-9]+[.,][0-9]{2})\s*(?:EUR|€)'`.
  * Deducción de categoría por palabras clave ("Repsol|Cepsa" -> Gasolina; "Mercadona|Carrefour|Lidl" -> Comida). Categoría por defecto: "Otros".

### Mejora 3: Simulador de Amortización Anticipada

* **Ruta**: `POST /api/v1/loans/{id}/simulate-prepayment`
* **Parámetros**: `prepayment_amount` (capital a amortizar) y `strategy` (`REDUCE_TERM` o `REDUCE_FEE`).
* **Cálculo**:
  * Intereses pendientes actuales vs. nuevo escenario tras amortización de capital.
  * Ahorro neto de intereses en euros.
  * Nueva fecha estimada de extinción o nueva cuota mensual reducida.

### Mejora 4: Presupuestos Máximos por Categoría

* En el tablero mensual y en la vista móvil:
  * Si `monthly_budget_limit` está definido para una categoría:
  * Calcular $GastoActual = \sum \text{amount (mes actual)}$.
  * Porcentaje consumido: $P = (GastoActual / Limit) \times 100$.
  * Barra de progreso y badges visuales: Verde ($P < 80\%$), Ámbar ($80\% \le P \le 100\%$), Rojo ($P > 100\%$).

### Mejora 5: Exportación Completa a CSV / Excel

* **Rutas**:
  * `GET /export/transactions.csv?year=YYYY&month=MM` (Exportación en CSV plano).
  * `GET /export/full-backup.xlsx` (Exportación de toda la BDD a un libro Excel estructurado con `openpyxl`).

## 6. Experiencia de Usuario y PWA (PashtAPP)

### 6.1. Configuración PWA (`manifest.json`)

```json
{
  "name": "PashtAPP - Finanzas Saeta",
  "short_name": "PashtAPP",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0f172a",
  "theme_color": "#0f172a",
  "icons": [
    {
      "src": "/static/icons/icon-192.png",
      "sizes": "192x192",
      "type": "image/png"
    },
    {
      "src": "/static/icons/icon-512.png",
      "sizes": "512x512",
      "type": "image/png"
    }
  ]
}
```

### 6.2. Vista Móvil (< 768px)

* **Header Fijo**: Dos indicadores en tiempo real: `En Cuenta` (verde) y `Proyectado` (azul).
* **FAB flotante (+)**: Despliega un panel inferior tipo Bottom-Sheet:
  * Teclado numérico grande (`inputmode="decimal"`).
  * Botonera rápida de categorías con iconos.
  * Selector de estado: "Cobrado ya" vs. diferido (2 días / mes prox / fraccionar).
* **Listado de conciliación rápida**: Checkbox táctil directo con HTMX (`hx-patch="/transactions/{id}/toggle-settled"`).

### 6.3. Vista Escritorio (>= 768px)

* **KPIs superiores**: Saldo Inicial, Ingresos Reales, Fijos Pagados, Variables Pendientes y Saldo Fin de Mes.
* **Layout 2 Columnas**:
  * Columna Izquierda: Tabla de Gastos Fijos recurrentes con estado de conciliación.
  * Columna Derecha: Tabla de Gastos Variables con buscador y selector de filtros.
* **Sección Analítica**: Gráfica donut mensual, matriz interanual de categorías y serie histórica de luz/kWh.
* **Módulo Préstamos**: Fichas de progreso de amortización para *Coche*, *Grueso* y *Placas* con modal interactivo del simulador.

## 7. Despliegue en Docker y Sistema de Copias de Seguridad

### Archivo `docker-compose.yml` (Portainer Ready)

```yaml
version: '3.8'

services:
  pashtapp-web:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: pashtapp_web
    restart: unless-stopped
    ports:
      - "8080:8000"
    environment:
      - APP_NAME=PashtAPP
      - APP_ENV=production
      - SECRET_KEY=replace_with_a_secure_random_key_64_bytes
      - DATABASE_URL=sqlite:////data/pashtapp.db
      - ADMIN_USERNAME=admin
      - ADMIN_PASSWORD_HASH=$2b$12$e0M2/qX0g8sY4wWf5.W1aeh10m... # Generar hash bcrypt
      - BACKUP_DIR=/backups
    volumes:
      - pashtapp_data:/data
      - pashtapp_backups:/backups
    networks:
      - pashtapp_net

  pashtapp-backup:
    image: alpine:3.20
    container_name: pashtapp_backup
    restart: unless-stopped
    depends_on:
      - pashtapp-web
    volumes:
      - pashtapp_data:/data:ro
      - pashtapp_backups:/backups
    entrypoint: |
      /bin/sh -c '
      apk add --no-cache sqlite tzdata
      cp /usr/share/zoneinfo/Europe/Madrid /etc/localtime
      echo "Servicio de backup de PashtAPP iniciado (03:00 AM Europe/Madrid)..."
      while true; do
        CURRENT_HOUR=$$(date +%H:%M)
        if [ "$$CURRENT_HOUR" = "03:00" ]; then
          TIMESTAMP=$$(date +%Y%m%d_%H%M%S)
          echo "[$$TIMESTAMP] Iniciando backup online de PashtAPP..."
          sqlite3 /data/pashtapp.db ".backup /backups/pashtapp_$$TIMESTAMP.db"
          gzip -f /backups/pashtapp_$$TIMESTAMP.db
          find /backups -name "pashtapp_*.db.gz" -mtime +30 -delete
          echo "[$$TIMESTAMP] Backup finalizado y comprimido con éxito."
          sleep 70
        fi
        sleep 30
      done
      '
    networks:
      - pashtapp_net

volumes:
  pashtapp_data:
  pashtapp_backups:

networks:
  pashtapp_net:
    driver: bridge
```

### Dockerfile Optimizado

```dockerfile
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN mkdir -p /data /backups

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Archivo `requirements.txt`

```text
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
sqlalchemy>=2.0.30
pydantic>=2.7.0
jinja2>=3.1.4
python-multipart>=0.0.9
passlib[bcrypt]>=1.7.4
python-jose[cryptography]>=3.3.0
openpyxl>=3.1.3
httpx>=0.27.0
```

## 8. Guía de Ejecución para el Agente

1. **Inicialización y Git**:
   ```bash
   git clone https://github.com/largoguns/pashtapp.git
   cd pashtapp
   ```
2. **Estructura**: Inicializar el árbol `app/{routers,models,templates,static}`, `scripts/` y Dockerfile.
3. **Persistencia**: Inicializar SQLite con `journal_mode=WAL` apuntando a `/data/pashtapp.db`.
4. **Migrador**: Ejecutar `python scripts/migrate_excel.py` apuntando a `Pashta Saeta 2026.xlsx`.
5. **Implementación de Endpoints Core & UI**: Integrar vistas Jinja2 con HTMX y Chart.js, manifest PWA y Service Worker.
6. **Validación**: Desplegar el stack Compose, validar la carga de datos del libro original y confirmar que las copias de seguridad se generan y comprimen adecuadamente.