# CreditIQ — Arquitectura AWS Serverless

## Descripción del Proyecto

**CreditIQ** es un ecosistema serverless de servicios financieros potenciados con IA, alojado en AWS.
El primer servicio es un **Analizador de Estados Financieros**: un flujo multi-agente que ingiere fichas técnicas y estados financieros de entidades, los procesa, y entrega un informe estandarizado, técnico y completo.

---

## Serverless vs Contenedores (ECS) — ¿Por qué Serverless es mejor aquí?

Dado tu background con contenedores, es normal dudar. Aquí te explico por qué **Serverless es la decisión correcta** para CreditIQ frente a ECS:

1. **Cero costo en reposo (Escalado a Cero)**: Un contenedor ECS (Fargate) corriendo 24/7 cuesta mínimo $15-30 USD/mes, incluso si nadie lo usa. Las Lambdas y Step Functions cuestan **exactamente $0** si no se ejecutan. Para iniciar proyectos, esto es oro.
2. **Naturaleza del problema (Event-driven)**: El análisis de estados financieros es un proceso asíncrono basado en eventos (ej. "el usuario sube un PDF a S3"). AWS Serverless maneja estos triggers nativamente sin que programes nada.
3. **Manejo de flujos complejos**: Usar Step Functions permite orquestar a los 4 agentes, definir qué pasa si uno falla (retries automáticos), y manejar tiempos de espera sin bloquear un servidor. Hacer esto en un contenedor requiere configurar colas (RabbitMQ/SQS), workers (Celery), y una base de datos de estado.
4. **Escalabilidad instantánea**: Si mañana necesitas procesar 100 análisis simultáneos, AWS levanta 100 Lambdas al instante. ECS tardaría minutos en instanciar nuevos contenedores.

> [!TIP]
> **Conclusión**: El esfuerzo inicial de aprender Serverless (usando AWS SAM) vale totalmente la pena por el ahorro en costos, la infraestructura gestionada y la escalabilidad infinita.

---

## Decisiones de Arquitectura Clave

> [!IMPORTANT]
> **Frontend: CloudFront + S3 (100% Serverless) — RECOMENDADO**
> Para una cuenta de prueba AWS, el costo es prácticamente **$0** en volúmenes bajos.

> [!NOTE]
> **Modelo de agentes: AWS Bedrock + Anthropic API Directa**
> El sistema estará preparado en Python para conectarse a **Amazon Bedrock (boto3)**, pero también tendrá un **módulo wrapper** para usar directamente la **API de Anthropic** usando tu API_KEY y créditos actuales. Se podrá alternar mediante variables de entorno (`LLM_PROVIDER=anthropic_api` o `LLM_PROVIDER=bedrock`).

> [!NOTE]
> **Documentos Híbridos**
> El Agente 1 (Extractor) usará librerías de Python (pandas/openpyxl) para Excel/CSV, y Amazon Textract (u OCR open source si buscamos reducir más el costo inicial) para PDFs.

> [!NOTE]
> **Preparado para Multi-tenant**
> Se diseñará la tabla de DynamoDB y el User Pool de Cognito con un atributo `tenant_id` (company_id). Aunque inicies como equipo pequeño, escalar a SaaS B2B no requerirá refactorizar la base de datos ni la autenticación.

---

## Estructura de Carpetas — Backend

*Nota: Usaremos **AWS SAM (Serverless Application Model)**. Es una extensión de CloudFormation que usa YAML simple para definir funciones Lambda, APIs y bases de datos. Es la forma más fácil de aprender Serverless.*

```
iastronauts_creditiq_back/
├── .env.example
├── README.md
├── requirements.txt                    # deps de desarrollo compartidas
├── template.yaml                       # AWS SAM — definición de la infraestructura
│
├── src/                                
│   │
│   ├── shared/                         # Código compartido (Layers de Lambda)
│   │   ├── llm_provider.py             # Wrapper: Bedrock vs Anthropic API Directa
│   │   ├── config.py                   
│   │   └── models/                     
│   │
│   ├── services/                       # Endpoints API Gateway
│   │   ├── auth/                       # Cognito triggers / gestión usuarios
│   │   ├── analysis/                   # Sube doc, inicia Step Function
│   │   └── reports/                    # Descarga reporte Markdown
│   │
│   └── agents/                         # Step Functions Tasks (Agentes)
│       ├── document_extractor/         # Hybrid: PDF (Textract) y Excel (Pandas)
│       ├── financial_analyzer/         
│       ├── risk_scorer/                
│       └── report_generator/           # Genera .md
│
├── step_functions/
│   └── analysis_workflow.json          # Flujo de los agentes
│
└── tests/
```

*(La estructura del frontend se mantiene como en el plan original usando React/Vite en S3+CloudFront).*

---

## Plan de Implementación por Fases

### Fase 0 — Fundamentos (Esta semana)
- [ ] Inicializar AWS SAM y crear estructura `src/`.
- [ ] Implementar `llm_provider.py` para soportar Anthropic API (usando tu API_KEY) y Bedrock.
- [ ] Definir recursos IaC en `template.yaml` (DynamoDB con tenant_id, S3, Cognito).

### Fase 1 — MVP del Analizador (2–3 semanas)
- [ ] Lambda `analysis-start` + upload a S3.
- [ ] Orquestación Step Functions.
- [ ] Agentes (1 al 4) ejecutando el flujo completo y generando el archivo `.md`.

### Fase 2 — Frontend (2 semanas)
- [ ] Setup React + Vite.
- [ ] Login/Auth.
- [ ] Subida de archivos (PDF/Excel) y visualización del Markdown.

---

## ¿Aprobación Final?

> [!IMPORTANT]
> **Revisa esta actualización.** Si estás de acuerdo con usar Serverless (AWS SAM), el soporte híbrido Bedrock/Anthropic API, y el enfoque multi-tenant desde el día 1, **dame tu aprobación (ej. "Aprobado, empieza a crear las carpetas")** para pasar a la etapa de ejecución (creación de código y estructura).