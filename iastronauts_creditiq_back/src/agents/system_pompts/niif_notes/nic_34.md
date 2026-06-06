# NIC 34 — Información Financiera Intermedia

## Propósito

NIC 34 establece el contenido mínimo de un informe financiero intermedio y los principios para el reconocimiento y medición en los estados financieros intermedios. Se aplica a las entidades que elaboran o presentan estados financieros para un período inferior a un ejercicio completo (trimestre, semestre, período de nueve meses, etc.).

## Requisitos de revelación

- Estado de situación financiera condensado comparado con el cierre del ejercicio anual anterior (no con el mismo período intermedio del año previo).
- Estado de resultados condensado comparado con el mismo período intermedio del ejercicio anterior y con el período acumulado desde el inicio del ejercicio hasta la fecha.
- Estado de cambios en el patrimonio neto comparado con el período comparable del ejercicio anterior.
- Estado de flujos de efectivo acumulado desde el inicio del ejercicio comparado con el mismo período acumulado del año anterior.
- Notas explicativas que incluyan: políticas contables, estacionalidad de las operaciones, partidas inusuales, cambios en estimaciones, emisiones o recompras de instrumentos de deuda o patrimonio, dividendos pagados, eventos posteriores, adquisiciones y desinversiones, cambios en pasivos contingentes.
- Base comparativa: el balance general se compara contra el cierre del año anterior (diciembre); el estado de resultados se compara contra el mismo período intermedio del año anterior.

## Base comparativa obligatoria (tabla de referencia)

| Estado                          | Período actual                | Período comparativo IFRS         |
|---------------------------------|-------------------------------|----------------------------------|
| Estado de situación financiera  | Fecha de cierre intermedio    | Cierre del último ejercicio anual (diciembre del año anterior) |
| Estado de resultados            | Acumulado período actual      | Mismo período acumulado del año anterior |
| Flujos de efectivo              | Acumulado período actual      | Mismo período acumulado del año anterior |
| Cambios en patrimonio           | Período del informe           | Período comparable del año anterior |

## Checklist — qué revelar en estados intermedios

- [ ] Balance comparado con el cierre del ejercicio anual inmediatamente anterior (no con el intermedio previo).
- [ ] Estado de resultados del período (trimestre/semestre) y del acumulado año a la fecha, comparados contra los mismos períodos del año anterior.
- [ ] Flujos de efectivo acumulados desde el inicio del ejercicio comparados con el mismo acumulado del año anterior.
- [ ] Indicación explícita de que las políticas contables utilizadas son consistentes con las del último ejercicio anual.
- [ ] Descripción de la naturaleza y monto de partidas inusuales por su naturaleza, tamaño o incidencia.
- [ ] Efecto de los cambios en la composición del grupo durante el período intermedio (fusiones, adquisiciones, escisiones).
- [ ] Para entidades con estacionalidad marcada: advertencia sobre la no representatividad del período para extrapolar resultados anuales.
- [ ] Cambios en estimaciones contables significativas respecto al período anual anterior.

## Señales de alerta para validación automática

- Período de cierre distinto de diciembre (mes ≠ 12) → aplicar NIC 34; la comparativa del balance debe ser el diciembre inmediatamente anterior, no el mismo mes del año previo.
- Si se detectan dos períodos con el mismo mes de cierre (ej. junio 2025 vs junio 2024) y son ingresos/flujos → cumple NIC 34; si son activos/pasivos → verificar que no se compare con el intermedio en lugar del año completo.
- Ausencia de período comparativo → ERROR grave bajo NIC 34; el informe no puede calificarse como conforme a IFRS.
