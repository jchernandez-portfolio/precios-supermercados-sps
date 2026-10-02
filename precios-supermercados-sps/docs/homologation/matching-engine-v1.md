# Motor de homologación probabilístico v1 (shadow)

Estado: **shadow**. El motor produce candidatos, scores, clusters, una cola de
revisión humana y métricas de evaluación. **No cambia la comparabilidad
publicada** (Consumer Catalog v3, marts, Power BI). Sólo las decisiones humanas
importadas al registro privado `config/homologation/reviewed-decisions-v1.json`
pueden llegar a persistencia, y la publicación sigue bloqueada por
`config/homologation/identity-policy-v1.yaml` (`publication.mode: shadow`,
`public_serving_allowed: false`).

Código: `src/precios_supermercados/matching/` · CLI:
`scripts/homologacion_motor_shadow.py` · Config:
`config/homologation/matching-engine-v1.yaml` y
`config/homologation/source-category-taxonomy-v1.yaml` · Pruebas:
`tests/test_matching_*.py`.

## 1. Por qué un motor de resolución de entidades

La identidad vigente es determinista: un GTIN válido compartido
(`prod_gtin_<14>`) más la normalización v2.x y un score heurístico
(0.20 tipo + 0.25 presentación + 0.20 marca + 0.45 nombre, umbral 0.72) que sólo
ordena la cola de revisión. Eso deja fuera de la comparación a las cadenas sin
GTIN publicado (Colonial, Comisariato Los Andes, PriceSmart) y no mide su
propia precisión.

La práctica de la industria para este problema (*record linkage* / *entity
resolution* / *product matching*) separa el trabajo en etapas medibles. Este
motor implementa esas etapas con dependencias mínimas (Python puro + PyYAML ya
presente en `requirements.txt`):

```text
estandarización → blocking → generación de candidatos
→ vectores de comparación → scoring probabilístico (Fellegi–Sunter)
→ umbrales precision-first (auto / revisión / no-match)
→ clustering con restricciones (≤ 1 oferta por contexto de cadena)
→ revisión humana (cola + importador al registro) → evaluación (silver + golden)
```

## 2. Referencias de industria

| Técnica | Referencia | Uso en el motor |
| --- | --- | --- |
| Modelo probabilístico de linkage | Fellegi & Sunter, *A Theory for Record Linkage* (JASA, 1969); Winkler (EM, Jaro–Winkler) | `fellegi_sunter.py`: m/u por nivel, peso log2, probabilidad, EM para λ y m |
| Linkage a escala con m/u, EM y "waterfall" | **Splink** (UK Ministry of Justice) | niveles discretos por campo, u por muestreo, EM con u fijo, explicación término a término, reglas de blocking en unión |
| Aprendizaje con etiquetas y clustering | **Dedupe** (dedupe.io) | etiquetas para entrenar/evaluar, predicados de blocking por índice, revisión activa de la banda dudosa |
| Llave primaria de producto | **GS1 GTIN** (General Specifications): GTIN-8/12/13/14, check digit, números de circulación restringida (02x, 04x, 20–29; EAN-8 0xx/2xx) y cupones (98x–99x) | `gtin.py`: GTIN válido y no restringido = identidad determinista; restringidos nunca cuentan |
| Blocking / indexación | Christen, *Data Matching* (2012); MinHash (Broder, 1997) y LSH (Indyk–Motwani) | `blocking.py`: unión de llaves + MinHash-LSH sobre shingles de caracteres |
| Recuperación por similitud ("embedding retrieval") | Motores de product matching de retail (Amazon, Walmart, Google Shopping) recuperan vecinos por embeddings/ANN antes de un clasificador por pareja | MinHash-LSH + TF-IDF de n-gramas cumple el mismo rol (vecinos top-k por cadena) sin modelos pesados |
| Agrupación de ofertas en productos | Comparadores de precio (Google Shopping, etc.): un producto agrupa ofertas, como máximo una por comercio/tienda | `clustering.py`: union-find con restricción ≤ 1 oferta por contexto y linkage completo |
| Umbrales precision-first | Práctica estándar de catálogos: auto-match sólo con precisión medida alta, banda media a revisión | `thresholds.py`: t_auto con precisión ≥ 0.98 por par de cadenas |
| Evaluación con golden set | Muestreo estratificado por banda y segmento con pesos de estrato | `golden.py`: ≥ 500 parejas, formato de etiquetado, precisión/recall/F1 por banda y par |

## 3. Etapas

### 3.1 Estandarización (`standardize.py`)

Reutiliza la normalización vigente (`profile_product_v2`: marca canónica,
presentación, taxonomía por nombre) y agrega:

- separación de palabras pegadas (`AbrazosVainilla`, `Botell330ml`), frecuente en Colonial;
- sinónimos/abreviaturas de catálogo (`gaseosa→refresco`, `deterg→detergente`, `pvo→polvo`);
- nombre núcleo: tokens sin marca, unidades, envase, números ni stopwords, con stemming ligero;
- códigos (`No.55`, `V8`, `3 en 1`) separados del tamaño;
- atributos de variante por familia (azúcar/línea, grasa, lactosa, sabor, aroma, color, audiencia, pulpa, envase, proteína…);
- tamaño canónico (g, ml, unidades; `oz` se conserva como onza);
- **tipo de producto desde la ruta de categoría fuente**: departamento por mapa,
  reglas de palabras clave por segmento (con control de departamento: "Agua
  micelar" no es Agua) y propagación del tipo dominante por hoja (≥ 5 ejemplos,
  ≥ 80 % de acuerdo). Ver `config/homologation/source-category-taxonomy-v1.yaml`;
- GTIN de identidad (válido y no restringido) y precio unitario (HNL/kg, HNL/L, HNL/unidad).

### 3.2 Blocking y candidatos (`blocking.py`)

Unión de reglas (ninguna es obligatoria): marca + bucket logarítmico de tamaño
(±1), tipo + bucket (±1), marca + primer token, dos primeros tokens +
dimensión; más **MinHash-LSH** (48 permutaciones, 16 bandas) sobre shingles de
3 caracteres de marca + nombre núcleo. Los bloques/cubetas gigantes se omiten.
Cada registro se queda con sus `top_k = 6` vecinos por cadena contraria según
Jaccard estimado + bonos de marca/tamaño. Siempre se agregan nombre exacto y
"gemelos" del mismo supermercado en otra tienda (TGU tiene un producto fuente por
tienda). Sólo se generan parejas entre supermercados distintos.

### 3.3 Vector de comparación (`comparison.py`)

| Campo | Niveles |
| --- | --- |
| marca | exacta · difusa · cruzada en nombre (submarca/fabricante) · en el nombre del otro · falta de un lado · faltan ambas · conflicto |
| nombre | muy alta · alta · media · baja · muy baja (0.5·coseno TF-IDF de n-gramas + 0.5·Dice suave de tokens) |
| diferencia de nombre | ninguna · extra menor de un lado · extra raro de un lado · ambos lados menor · ambos lados con tokens raros |
| tamaño | exacto (≤ 0.5 % o 1.5 u) · ≤ 2 % · ≤ 6 % · puente onza (oz↔g/ml ≤ 3 %) · falta · conflicto |
| pack | mismo multipack · ambos unitarios · falta · conflicto |
| variante | coincide · ninguna · un solo lado · parcial · conflicto (valores por defecto implícitos como "Original" no cuentan como un solo lado) |
| códigos | coinciden · ninguno · un lado · parcial · conflicto |
| tipo | mismo tipo · relacionado · mismo departamento · desconocido · conflicto de departamento · conflicto de tipo |
| precio | ratio de precio unitario ≤ 1.3 · ≤ 2 · ≤ 3 · > 3 · falta — **evidencia débil, peso acotado a ±1 bit** |
| GTIN | igual · distinto · restringido · falta — **regla determinista fuera del modelo** |

El GTIN no entra al modelo: así la evaluación con etiquetas GTIN es ciega al
GTIN y mide lo que el motor aporta donde no hay código.

### 3.4 Modelo Fellegi–Sunter (`fellegi_sunter.py`)

- `m` = frecuencia de cada nivel entre **parejas silver positivas** (dos cadenas
  distintas comparten un GTIN válido no restringido), split de entrenamiento.
- `u` = frecuencia entre **negativos silver duros** dentro de los candidatos
  (exclusión 1:1: A tiene su pareja GTIN B en la cadena de C, C ≠ B y C tiene otro
  GTIN). Estimar `u` dentro de la población bloqueada corrige la correlación
  marca–nombre entre no-matches de un mismo bloque. El `u` clásico de Splink
  (parejas aleatorias) se guarda como diagnóstico.
- Paiz–Walmart (misma plataforma, nombres idénticos) aporta ~75 % de las
  etiquetas. Se evaluó ponderarlas para que cada par de cadenas aporte lo
  mismo (`model.balance_retailer_pairs`): ganó 2–3 pts de recall en pares con
  La Colonia/Colonial pero perdió 1.5 pts de precisión en prueba y cuadruplicó
  la banda de revisión, así que queda desactivado. Los umbrales por par de
  cadenas compensan la mezcla en la decisión.
- `λ` = EM sobre todas las parejas candidatas con m/u fijos. También se entrena
  una variante con EM completo de `m` (`model-em.json`) para comparar.
- Peso = log2(λ/(1−λ)) + Σ log2(m/u); probabilidad = 2^w/(1+2^w). Cada fila de la
  cola guarda el *waterfall* (nivel, m, u, factor de Bayes, peso por campo).

### 3.5 Capa de decisión y umbrales (`pipeline.py`, `thresholds.py`)

1. **Mismo GTIN válido no restringido** → auto-match determinista
   (`EXACT_TRADE_ITEM`); con contradicción material → revisión.
2. **Conflicto duro** (marca, tamaño, pack, variante, códigos, tipo, GTIN
   distinto) → no-match. Ningún score lo compensa (política v1).
3. **Topes de auto-match**: tamaño ≤ 6 %, puente de onzas, tamaño ausente, marca
   ausente, variante o código declarado de un solo lado → como máximo revisión
   (política: "oz" no se convierte sin `fl oz`; un atributo omitido no es
   evidencia).
4. Bandas por probabilidad: **auto-match** si p ≥ t_auto(par de cadenas) y sin
   topes; **revisión** si p ≥ t_review; si no, no-match.

Etiquetas silver divididas por hash del GTIN: 60 % entrenamiento, 20 %
calibración, 20 % prueba. `t_auto` se elige en calibración **por par de
cadenas** (un umbral global quedaría dominado por Paiz–Walmart, que comparten
plataforma y nombres) como el menor umbral cuyo **límite inferior de Wilson**
(z = 1.645, 95 % unilateral) de la precisión es ≥ 0.98 con soporte ≥ 50. Un par con etiquetas
pero sin soporte suficiente prueba el umbral más estricto en sus propias
etiquetas y, si no llega a 0.98, queda **sin auto-match** (todo a revisión).
Los pares sin etiquetas (PriceSmart sin GTIN; Comisariato hasta acumular
etiquetas con su GTIN reconstruido desde v2.6) **no tienen
auto-match** porque su precisión no está medida: sus parejas sobre el umbral más
estricto observado se reportan como "alta confianza no medida" y van a revisión
con prioridad hasta etiquetar el golden set. `t_review` es el menor umbral con precisión acumulada ≥ 0.5 (piso 0.05) y
la cola se limita a 2 candidatos por registro y cadena. Las métricas se reportan
sobre el split de prueba.

### 3.6 Clustering con restricciones (`clustering.py`)

Aristas auto-match (GTIN primero, luego por peso) con union-find. Una unión sólo
ocurre si no repite contexto de cadena (supermercado + tienda), no supera 8
miembros y, con `linkage: complete`, **todas** las parejas cruzadas son
auto-match: la transitividad no se presume (regla 7 del estándar). Los
componentes que violarían esto se parten por sus aristas más fuertes.

### 3.7 Precio unitario y `COMPARABLE_ALTERNATIVE` (`unit_price.py`)

Misma marca, nombre muy similar, sin conflicto de variante, pero tamaño distinto
→ fila `COMPARABLE_ALTERNATIVE` con precio por kg/L/unidad y cuál es más barato
por unidad. **Nunca es identidad** y no alimenta el ahorro "mismo producto".

## 4. Revisión humana (`review.py`)

`review-queue.csv`/`.jsonl` contiene ambas ofertas (nombre, marca,
presentación, categoría, precio, precio unitario, GTIN, huella de evidencia),
probabilidad, peso, niveles, waterfall, decisión sugerida y
`suggested_master_product_id` (GTIN publicado si existe; si no,
`prod_verified_<hash del cluster>`). Se limita a 2 candidatos por registro y
cadena contraria.

El revisor llena `review_decision` (`same_product`, `different_products`,
`variant`, `alternative`, `pending`), `evidence_codes`, `evidence_references`,
`rationale`, `reviewed_by`, `reviewed_at_utc`. El importador
(`import-review`) valida contra `ReviewedIdentityDecision`:

- `same_product` exige `human_verified` + (`barcode_visible` |
  `manufacturer_catalog` | `same_valid_gtin` | `package_front`+`package_back`);
- las huellas deben venir de Turso (`fingerprint_authority = turso_products`);
  las colas reconstruidas offline se rechazan porque quedarían obsoletas;
- por defecto es *dry-run*; `--write-registry` fusiona de forma atómica y
  revalida el registro completo.

El registro sigue siendo privado; publicar identidades revisadas requiere un
cambio explícito de política (gate) además de precisión medida.

## 5. Evaluación (`golden.py`)

- **Silver (automática)**: precisión/recall/F1 por banda y por par de cadenas en
  el split de prueba, recall de blocking ciego al GTIN.
- **Golden (humana)**: `golden-set.csv` con ≥ 500 parejas estratificadas por
  banda (35 % auto, 40 % revisión, 25 % no-match cercano) y par de cadenas, con
  tamaño de estrato para estimar precisión ponderada a la población. Etiquetas:
  `match`, `no_match`, `variant`, `alternative`, `unsure`. La etiqueta silver va
  en un archivo aparte para no sesgar. `evaluate-golden` reporta precisión
  (con límite inferior de Wilson), recall y F1 por banda y par.
- **Cobertura**: % de registros fuente en identidades con ≥ 2 cadenas por
  ciudad, actual vs proyectado (auto-match) vs cota superior si la cola revisada
  se confirmara (asignación 1:1).

## 6. Operación

```bash
# Offline sobre registros reconstruidos (JSONL precios-sps-matching-record/v1)
python scripts/homologacion_motor_shadow.py run --records registros.jsonl --output-dir salida/
# Contra Turso (solo lectura; mismas credenciales que la exportación de revisión)
python scripts/homologacion_motor_shadow.py run --turso --output-dir salida/
# Evaluar un golden set etiquetado
python scripts/homologacion_motor_shadow.py evaluate-golden --golden salida/golden-set.csv
# Convertir una cola revisada (dry-run; agregar --write-registry para fusionar)
python scripts/homologacion_motor_shadow.py import-review --reviewed revisada.csv
```

No está conectado al workflow diario ni a ningún workflow programado.

## 7. Resultado offline del corte 2026-09-21

Ver la sección "Motor de homologación (shadow)" en `docs/PROJECT_STATE.md` para
las cifras de la corrida. Las salidas completas son privadas y no se versionan.

## 8. Límites conocidos

- Las etiquetas silver sólo existen donde hay GTIN (Walmart, Paiz, La Colonia y
  Colonial vía su campo `reference`); Comisariato y PriceSmart no tienen
  etiquetas: no reciben auto-match hasta medir su precisión con el golden set.
- "GTIN distinto" no siempre es "producto distinto" (importado vs local,
  bolsa vs bote): la precisión silver es una cota inferior ruidosa.
- Fellegi–Sunter supone independencia condicional entre campos; nombre, marca y
  tipo están correlacionados. La calibración por umbral en datos retenidos
  compensa en la decisión, pero las probabilidades no deben leerse como
  frecuencias exactas.
- El rendimiento está dominado por `profile_product_v2` (búsqueda de marcas en
  el nombre recorre todo el léxico por registro); una corrida completa SPS+TGU
  (~99 k registros) tarda ~10 minutos.
- `--turso` lee `products` completo y agrupa `price_history` por producto y
  ubicación: con la cuota de lecturas de Turso agotada en septiembre, debe
  usarse sólo de forma puntual (o desde un export ya materializado).
- El campo `reference` de Colonial pasa el check digit GS1 en ~92 % de los
  productos y coincide con EAN de Walmart en ~2.9 k casos (snapshots
  2026-08-30/31). Hoy no se persiste como `ean`; el motor sólo lo usa como
  etiqueta silver. Promoverlo a GTIN de identidad es un cambio de scraper
  aparte.

## 9. Cómo se promovería (no habilitado)

1. Etiquetar el golden set (≥ 500 parejas) y confirmar precisión ≥ 0.98 en la
   banda auto por par de cadenas.
2. Revisar e importar decisiones desde una corrida `--turso` (huellas vigentes).
3. Agregar a la política `automatic_identity.allowed` la regla
   `probabilistic_auto_match_with_measured_precision` y cambiar
   `publication.mode` en un PR revisado. `PolicyGate.engine_publication_allowed`
   exige además la precisión medida.

## 10. Candidatos contra el producto maestro (2026-10-01)

Además de parejas producto↔producto, `matching/master_candidates.py` compara
cada producto no homologado contra los registros *golden* de
`master_products` (marca, tamaño ±2 %, pack, variante, tipo y nombre contra
los miembros) y produce una cola de revisión con `master_product_id`
(`scripts/exportar_cola_maestro.py`). Sigue en modo shadow: `engine_auto`
está deshabilitado en la política, un GTIN válido distinto siempre es
revisión y las decisiones humanas se importan con
`scripts/importar_decisiones_maestro.py` como vínculos `manual_review` o
rechazos. Ver [`product-master-v1.md`](product-master-v1.md).
