---
name: precios-supermercados-engineering
description: Workflow seguro de ingeniería, arquitectura, debugging, pruebas, revisión y mantenimiento para tareas relacionadas específicamente con precios-supermercados-sps/ y sus workflows asociados en .github/workflows/. Usar cuando Codex trabaje técnicamente en Precios de Supermercados SPS; no usar para otros proyectos del monorepositorio Portafolio.
---

# Precios Supermercados — Engineering Workflow

## Aplicar la gobernanza del repositorio

1. Localizar desde la raíz del repositorio todos los `AGENTS.md` aplicables al archivo o área de trabajo.
2. Leerlos antes de diseñar, modificar, probar o revisar. Considerar, como mínimo cuando corresponda, `precios-supermercados-sps/AGENTS.md` y `.github/workflows/AGENTS.md`.
3. Tratar sus restricciones como obligatorias y como fuente de verdad del estado operativo, las autorizaciones, las ramas, los PR y los límites vigentes.
4. Confirmar el alcance autorizado de la tarea antes de actuar.

`AGENTS.md` define **qué** está permitido. Esta Skill define **cómo** ejecutar el trabajo de ingeniería. Si existe conflicto, seguir la instrucción aplicable más restrictiva y reportarlo. No convertir datos temporales, historial, IDs, ramas, HEAD o PR actuales en reglas permanentes.

## Clasificar el riesgo

- **LOW RISK:** documentación y cambios menores sin comportamiento ejecutable.
- **NORMAL:** Python, lógica, pruebas y refactors controlados.
- **HIGH RISK:** workflows privilegiados, autorización, seguridad, tráfico live, scraping real, credenciales, trusted boundaries o persistencia productiva.

Para riesgo alto usar obligatoriamente:

`AUDIT → DESIGN → REVIEW DEL DISEÑO → IMPLEMENT → TEST → REVIEW DEL DIFF`

No saltar a implementación de alto riesgo sin instrucción humana explícita. Aplicar diseño conservador y fail-closed a seguridad, privilegios, workflows y operaciones live.

## Ejecutar el workflow

### 1. AUDIT

Antes de cambiar código:

- inspeccionar `git status`, rama, HEAD y diff existente;
- detenerse y reportar si hay cambios no atribuibles a la tarea;
- localizar los componentes ya implementados y evitar reconstrucciones o implementaciones paralelas;
- leer las pruebas y documentación relevantes;
- verificar el código y Git actuales;
- separar hechos verificados, contexto histórico no verificado, inferencias e hipótesis.

### 2. PLAN O DESIGN

Para tareas no triviales:

- definir el problema exacto y el criterio de aceptación;
- minimizar la superficie del cambio e identificar los archivos previstos;
- registrar riesgos y pruebas necesarias;
- reutilizar contratos y abstracciones existentes;
- evitar arquitectura adicional sin necesidad.

Cerrar el diseño con uno de estos gates:

- `DESIGN_READY_FOR_REVIEW`
- `DESIGN_NOT_READY`

Exigir revisión del diseño antes de implementar cambios de alto riesgo.

### 3. IMPLEMENT

- modificar únicamente lo autorizado;
- reutilizar contratos, módulos y abstracciones existentes;
- evitar implementaciones paralelas y scope creep;
- preservar compatibilidad cuando corresponda;
- mantener cambios pequeños, coherentes y trazables;
- no hacer commit, push, merge ni operaciones sobre PR salvo autorización expresa.

Cerrar la implementación con uno de estos gates:

- `IMPLEMENTATION_READY_FOR_REVIEW`
- `IMPLEMENTATION_NOT_READY`

### 4. TEST

- aplicar pruebas proporcionales al cambio y obedecer las instrucciones de `AGENTS.md`;
- mantener las pruebas offline salvo autorización live explícita y vigente;
- no contactar sitios externos del supermercado durante tests offline;
- no instalar dependencias ni browsers automáticamente cuando la tarea o `AGENTS.md` lo prohíban;
- distinguir tests ejecutados, tests no ejecutados, limitaciones del entorno, CI histórica y CI actual;
- no declarar una suite completa verde si no se ejecutó completa.

### 5. REVIEW

Después de una implementación no trivial, realizar una revisión adversarial independiente.

- **PRINCIPAL:** auditar, diseñar, implementar y probar cambios autorizados.
- **REVIEWER:** revisar preferentemente en modo read-only e intentar encontrar bugs, bypasses, contradicciones y regresiones.

Cuando haya subagentes disponibles, usar un reviewer separado, entregarle el diff o artefactos y el contexto mínimo necesario, y no anticiparle la respuesta esperada. El reviewer no debe modificar código salvo autorización específica. Si no hay reviewer independiente, hacer una segunda pasada explícita y, para riesgo alto, marcar esa revisión como pendiente.

Usar uno de estos veredictos:

- `ACCEPTED`
- `NEEDS_CHANGES`
- `REJECTED`
- `BLOCKED`

No usar `ACCEPTED` si queda un hallazgo `CRITICAL` o `HIGH` bloqueante sin resolver.

Clasificar hallazgos como `CRITICAL`, `HIGH`, `MEDIUM`, `LOW` o `INFO`. Para hallazgos importantes indicar archivo, símbolo, problema, impacto, evidencia y corrección mínima recomendada.

### 6. REMEDIATE

Si la revisión rechaza el cambio:

1. reproducir o verificar cada hallazgo sin defender automáticamente la implementación;
2. corregir solo los problemas confirmados;
3. volver a ejecutar las pruebas afectadas;
4. volver a someter el cambio a revisión.

No acumular correcciones críticas aislables sin una revisión intermedia.

### 7. REPORT

Reportar al final, según resulte relevante:

- rama y HEAD inicial/final;
- working tree y archivos modificados;
- tests ejecutados, resultados y tests bloqueados o no ejecutados;
- limitaciones del entorno y distinción entre CI histórica y actual;
- tráfico externo/live;
- commits y push;
- riesgos y hallazgos restantes;
- veredicto y siguiente paso;
- si el siguiente paso fue ejecutado.

## Controlar efectos live y externos

- prohibir efectos live o externos por defecto;
- leer `AGENTS.md` para determinar la autorización vigente;
- no tratar una tarea de diseño o infraestructura como autorización live;
- no confundir autorización de formato con autorización de ejecución;
- no ejecutar automáticamente un “siguiente paso” que requiera nueva autorización humana;
- no usar IDs live específicos codificados en esta Skill como fuente de verdad.

## Entregar continuidad

Terminar toda tarea técnica relevante con un bloque `RESUMEN PARA CONTINUAR EN CHATGPT`. Usar solo los campos relevantes, incluyendo como base:

```text
TAREA:
ROL:
RAMA:
HEAD INICIAL:
HEAD FINAL:
WORKING TREE:
ARCHIVOS MODIFICADOS:
TESTS:
RESULTADO:
LIMITACIONES:
TRÁFICO EXTERNO/LIVE:
HALLAZGOS:
VEREDICTO:
COMMITS:
PUSH:
SIGUIENTE PASO:
SIGUIENTE PASO EJECUTADO:
```

Agregar campos específicos únicamente cuando sean necesarios.
