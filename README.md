# MetBalance Pro

**Software de Conciliación Estadística de Balances Metalúrgicos**

Proyecto desarrollado como soporte computacional para la tesis de pregrado en Ingeniería de Minas:

> *"Optimización de la producción minera mediante el desarrollo de una herramienta de software para la conciliación de datos de balances metalúrgicos utilizando datos históricos de plantas de procesamiento a nivel mundial".*

---

## 1. Descripción General

**MetBalance Pro** es una aplicación de escritorio industrial construida en Python (`PySide6`, `NumPy`, `SciPy`, `Pandas`, `Pydantic v2`, `Matplotlib`, `openpyxl` y `ReportLab`) para formular, diagnosticar, conciliar estadísticamente y auditar balances de masa total y por especies químicas en circuitos de procesamiento de minerales, siguiendo los lineamientos metodológicos de **AMIRA P754** y la Teoría de Muestreo de Gy.

### Capacidades Principales

- **Editor Topológico y Matriz de Incidencia ($A$):** Construcción de diagramas de flujo dirigidos (norma ISO 10628 / P&ID) con 4 plantillas listas para usar (`concentrator_2prod`, `concentrator_3prod`, `polymetallic_4prod` y `multinode_rsc`).
- **Ingesta y Limpieza Visual Guiada (ETL):** Perfilado previo de archivos `.xlsx` y `.csv`/`.tsv` sin alteraciones silenciosas, detección de anomalías (comas decimales, texto corrupto, valores faltantes, negativos, leyes $> 100\%$, unidades mixtas), aislamiento de columnas operativas no metalúrgicas (`F80_um`, `P80_um`, granulometría) y aplicación bajo confirmación explícita con registro de auditoría y hash SHA-256.
- **Modelado de Incertidumbres ($\sigma$):** Presets industriales AMIRA P754, estimación empírica histórica o edición manual en tabla con validación estricta $\sigma > 0$.
- **Observabilidad y Grados de Libertad ($\nu$):** Descomposición QR/SVD de la matriz Jacobiana bilineal $B = [B_m \mid B_u]$ para clasificar variables (*Medida Redundante*, *Medida No Redundante*, *No Medida Observable*, *No Observable*), evaluar cierre nodal preliminar y bloquear sistemas subdeterminados ($\nu < 0$).
- **Motor de Conciliación Estadística WLS y Detección de Errores Groseros (GED):** Optimización no lineal por Mínimos Cuadrados Ponderados (`SLSQP` con respaldo `trust-constr`, escalado numérico y tolerancia de cierre $< 10^{-8}$), ejecución individual o por lotes históricos en hilo secundario (`QThread`), test global $\chi^2$, *z-scores* estandarizados y comparación interactiva **Con vs. Sin Outlier**.
- **KPIs Metalúrgicos y Visualización Técnica:** Cálculo comparativo Nominal vs. Reconciliado (Recuperación, *Mass Pull*, Razón de Concentración, Fino Contenido y Diferencia No Explicada), diagrama Sankey proporcional, gráfico de paridad ($\pm 2\sigma$), barras de ajuste relativo y tendencias temporales con carta de control $\chi^2$.
- **Reportes Corporativos Auditables:** Perfil corporativo persistente (empresa, unidad minera, responsable técnico, código documental, color primario y logotipo) para exportar libros Excel multi-hoja con fórmulas nativas (`openpyxl`) e informes técnicos PDF A4 en 300 DPI (`ReportLab`) con sello de trazabilidad AMIRA P754.
- **Interfaz CAD-Industrial Bilingüe (`ES` / `EN`):** Paneles acoplables y flotantes (`QDockWidget`) con persistencia en `QSettings`, cambio de idioma en caliente y temas Claro/Oscuro técnicos.

---

## 2. Arquitectura por Capas

El proyecto separa estrictamente la lógica matemática y de datos (*headless*, sin dependencia de Qt) de la capa gráfica de escritorio:

```text
metbalance_pro/
├── pyproject.toml                   # Configuración de pytest (pythonpath = ["."], testpaths = ["tests"])
├── requirements.txt                 # Dependencias del proyecto
├── README.md                        # Documentación técnica y guía de ejecución
├── core/                            # Núcleo matemático y ETL (NUNCA importa PySide6)
│   ├── models.py                    # Esquemas Pydantic v2, enums de dominio y conversión de leyes
│   ├── topology.py                  # Matriz de incidencia A, validación topológica y 4 plantillas
│   ├── observability.py             # Jacobiano bilineal, rangos QR/SVD, DOF y desbalance nodal
│   ├── reconciliation.py            # Motor WLS (SLSQP / trust-constr), lotes y Con vs. Sin Outlier
│   ├── statistics_ged.py            # Test global Chi-cuadrado, covarianza de ajustes y z-scores
│   ├── kpis.py                      # Recuperaciones, Mass Pull, razón de concentración y finos
│   ├── etl.py                       # Perfilado de Excel/CSV, limpieza confirmada, presets AMIRA/empírico
│   └── sample_generator.py          # Generador determinístico (seed=42) de Dataset A y Dataset B
├── app/                             # Capa de aplicación de escritorio (PySide6)
│   ├── main.py                      # Punto de entrada (`python -m app.main`)
│   ├── settings_manager.py          # Persistencia QSettings (geometría, docks, tema, idioma, branding)
│   ├── i18n/
│   │   ├── es.json                  # Diccionario en español
│   │   ├── en.json                  # Diccionario en inglés (100% paridad de claves y placeholders)
│   │   └── manager.py               # Gestor reactivo puro Python (I18nManager, sin PySide6)
│   ├── assets/
│   │   ├── icons/                   # 25 íconos SVG técnicos (trazo 1.5px #334155) + símbolos ISO 10628
│   │   ├── styles/                  # engineering_light.qss y engineering_dark.qss
│   │   └── sample_data/             # Datasets precargados (.xlsx y .csv)
│   └── ui/                          # Ventana principal, docks y vistas de los 7 pasos
│       ├── main_window.py
│       ├── project_tree_dock.py
│       ├── inspector_dock.py
│       ├── flowsheet_canvas.py
│       ├── project_topology_view.py
│       ├── etl_wizard_view.py
│       ├── uncertainty_view.py
│       ├── diagnostics_view.py
│       ├── solver_results_view.py
│       ├── charts_view.py
│       ├── reporting_view.py
│       └── ui_common.py
├── reporting/                       # Motor de reportes corporativos (headless, sin PySide6)
│   ├── branding.py                  # Persistencia JSON del perfil corporativo y logotipo por defecto
│   ├── excel_exporter.py            # Libros openpyxl multi-hoja con fórmulas nativas y gráficos
│   └── pdf_exporter.py              # Informes ReportLab A4 con gráficos a 300 DPI y sello AMIRA P754
├── scripts/
│   └── generate_icons.py            # Generador de los 25 activos vectoriales SVG
└── tests/                           # Suite automatizada pytest + pytest-qt
    ├── test_topology_observability.py
    ├── test_reconciliation_math.py
    ├── test_etl_cleaning.py
    ├── test_reporting.py
    ├── test_i18n_completeness.py
    └── test_ui_smoke.py
```

---

## 3. Instalación y Ejecución en Windows

Todos los comandos siguientes se ejecutan desde el directorio `metbalance_pro/` usando PowerShell en Windows (requiere Python 3.11 o superior; probado en Python 3.15.1):

### 3.1. Crear y activar el entorno virtual `.venv`

```powershell
cd metbalance_pro
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3.2. Instalar dependencias

```powershell
pip install -r requirements.txt
```

### 3.3. Lanzar la aplicación de escritorio

```powershell
python -m app.main
```

### 3.4. Ejecutar la suite completa de pruebas (`pytest`)

En una sesión interactiva de escritorio en Windows:

```powershell
pytest -v
```

En entornos sin servidor gráfico (*headless*, por ejemplo CI o consola remota):

```powershell
$env:QT_QPA_PLATFORM="offscreen"; pytest -v
```

### 3.5. Regenerar los datasets de prueba y los íconos SVG

```powershell
python -m core.sample_generator
python -m scripts.generate_icons
```

---

## 4. Flujo de Ingeniería Secuencial (7 Pasos)

El panel **Árbol de Proyecto Secuencial** guía al usuario en siete etapas con indicadores de estado (`[Pendiente]`, `[Con Advertencias]`, `[Validado]`, `[Bloqueado]`) y bloqueo de precondiciones:

1. **Paso 01 — Proyecto y Topología:** Selección de unidad másica (`t/h`, `t/d`, `TMS/guardia`), especies químicas (`%`, `g/t`, `ppm`, `oz/t`), plantilla de circuito o edición interactiva de nodos/corrientes en `FlowsheetCanvas` e inspección en vivo de la matriz de incidencia $A$.
2. **Paso 02 — Ingesta y Limpieza ETL:** Carga de archivos `.xlsx`/`.csv` o datasets precargados, perfilado automático de anomalías y columnas no metalúrgicas, mapeo asistido a corrientes/especies y confirmación obligatoria del `CleaningPlan` antes de transformar datos.
3. **Paso 03 — Modelo de Incertidumbres ($\sigma$):** Configuración de desviaciones estándar relativas (`% RSD`) o absolutas mediante presets AMIRA P754, estimación empírica histórica o edición manual ($\sigma > 0$), más nivel de significancia $\alpha$.
4. **Paso 04 — Diagnóstico Pre-Conciliación:** Cálculo de rango y grados de libertad ($\nu$) sobre el Jacobiano bilineal, clasificación de observabilidad de variables y cierre nodal crudo preliminar. Si el sistema es subdeterminado ($\nu < 0$ o existen variables no observables), el Paso 05 queda bloqueado indicando las mediciones faltantes.
5. **Paso 05 — Motor WLS y Test GED:** Conciliación no lineal para un período o para todo el lote histórico en `QThread` con barra de progreso, diagnóstico $\chi^2$ / *z-score* y herramienta de exclusión/comparación **Con vs. Sin Outlier**.
6. **Paso 06 — Visualización y KPIs:** Tabla comparativa Nominal vs. Reconciliado (*Mass Pull*, Razón de Concentración, Fino Contenido, Recuperación y Diferencia No Explicada) junto con 4 pestañas gráficas: Flujograma Sankey, Gráfico de Paridad ($\pm 2\sigma$), Ajustes Relativos (%) y Tendencias / Carta de Control $\chi^2$.
7. **Paso 07 — Reportes Corporativos:** Configuración persistente de empresa, unidad minera, responsable, código documental, color corporativo y logotipo, con exportación directa a `.xlsx` (5 hojas con fórmulas nativas y gráficos) y `.pdf` (A4 auditable con gráficos a 300 DPI).

---

## 5. Datasets Precargados

Los archivos de ejemplo residen en `app/assets/sample_data/` y se generan de forma reproducible (`seed=42`) mediante `core/sample_generator.py`:

| Dataset | Archivos | Circuito | Alcance y Anomalías Controladas |
| :--- | :--- | :--- | :--- |
| **Dataset A** | `dataset_a_polimetalico_4prod.xlsx` / `.csv` | `polymetallic_4prod` (1 nodo: `PLANT`; corrientes `Cabeza`, `ConCu`, `ConPb`, `ConZn`, `Relave`; especies `Ag` g/t, `Cu` %, `Pb` %, `Zn` %) | **60 guardias** (`G-001` a `G-060`) en `TMS/guardia`. Incluye 4 columnas operativas de molienda (`F80_um`, `P80_um`, `Pct_Mas_65M`, `Pct_Menos_200M`), comas decimales, celdas vacías, texto corrupto (`N/D`, `ERR`), un valor negativo (`-0.08`), una ley fuera de rango (`105.4`) y **3 errores groseros inyectados** en `G-015`, `G-034` y `G-051`. |
| **Dataset B** | `dataset_b_multinodo_rsc_cu_mo.xlsx` / `.csv` | `multinode_rsc` (4 nodos: `MIX`, `RO`, `SC`, `CL`; 8 corrientes con cargas circulantes; especies `Cu` %, `Mo` %, `Fe` %) | **30 días** (`D-01` a `D-30`) en `t/h`. Mantiene 4 flujos circulantes internos sin medidor de flujo (`RO_conc`, `RO_tail`, `SC_conc`, `CL_tail`) que el motor clasifica como *No Medida Observable*, e inyecta un sesgo sistemático de muestreador en `RO_tail` (`Cu`) entre los días `D-18` y `D-25`. |
