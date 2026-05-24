# CreditIQ — Guía Completa de Despliegue

> AI Innovation Challenge 2026 — BTG Pactual Colombia  
> Stack: AWS SAM · Python 3.12 · React 19 + Vite · GitHub Actions

---

## Índice

1. [Estructura del repositorio](#1-estructura-del-repositorio)
2. [Configuración inicial de GitHub](#2-configuración-inicial-de-github)
3. [Configuración de AWS — cuenta y permisos](#3-configuración-de-aws--cuenta-y-permisos)
4. [Secrets y variables de entorno](#4-secrets-y-variables-de-entorno)
5. [Configuración de Cognito](#5-configuración-de-cognito)
6. [Build y deploy del backend (SAM)](#6-build-y-deploy-del-backend-sam)
7. [Build y deploy del frontend](#7-build-y-deploy-del-frontend)
8. [Pipelines CI/CD con GitHub Actions](#8-pipelines-cicd-con-github-actions)
9. [Ambientes (dev / uat / prod)](#9-ambientes-dev--uat--prod)
10. [Smoke tests post-deploy](#10-smoke-tests-post-deploy)
11. [Rollback](#11-rollback)
12. [Referencia rápida de comandos](#12-referencia-rápida-de-comandos)

---

## 1. Estructura del repositorio

```
03_iastronauts/                          ← raíz del monorepo
│
├── .github/
│   └── workflows/
│       ├── backend.yml                  ← pipeline backend (SAM)
│       └── frontend.yml                 ← pipeline frontend (Vite → S3)
│
├── iastronauts_creditiq_back/           ← backend AWS SAM
│   ├── template.yaml                    ← definición de toda la infraestructura
│   ├── samconfig.toml                   ← configuración de ambientes SAM
│   ├── step_functions/
│   │   └── analysis_workflow.json       ← pipeline de agentes Step Functions
│   ├── src/
│   │   ├── requirements.txt             ← dependencias empaquetadas en Lambda
│   │   ├── agents/                      ← handlers de cada agente
│   │   ├── api/                         ← handlers de la API HTTP
│   │   └── shared/                      ← modelos, LLM provider, middleware
│   └── tests/
│
├── iastronauts_creditiq_front/          ← frontend React + Vite
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
│
├── .gitignore
├── CLAUDE.md
├── deployment.md                        ← este archivo
└── README.md
```

### .gitignore definitivo

El `.gitignore` de la raíz debe cubrir ambos sub-proyectos:

```gitignore
# Secrets y entornos locales
.env
.env.local
.env.*.local

# Claude Code
.claude/

# Python / SAM
venv/
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.aws-sam/
samconfig.toml        # no versionar — cada dev tiene su propio

# Node / Frontend
node_modules/
dist/
*.local

# Sistema operativo
.DS_Store
Thumbs.db

# IDEs
.vscode/settings.json
.idea/
```

> **Nota:** `samconfig.toml` se genera con `sam deploy --guided` y contiene valores de cuenta específicos. Cada desarrollador genera el suyo localmente. El pipeline de CI/CD usa variables de entorno en lugar de este archivo.

---

## 2. Configuración inicial de GitHub

### 2.1 Crear el repositorio

```bash
# Si aún no existe el repo remoto
git init
git remote add origin https://github.com/TU_ORG/creditiq.git
git branch -M main
git push -u origin main
```

### 2.2 Estrategia de branches

```
main        ← producción. Solo merges desde uat via PR revisado.
uat         ← staging. Solo merges desde feature/* via PR.
feature/*   ← desarrollo de features. Se abre PR hacia uat.
hotfix/*    ← correcciones urgentes. PR directo a main + back-merge a uat.
```

**Reglas de branch en GitHub** (`Settings → Branches → Branch protection rules`):

| Branch | Regla |
|--------|-------|
| `main` | Require PR · Require 1 review · Require status checks · No direct push |
| `uat`  | Require PR · Require status checks · No direct push |

### 2.3 GitHub Environments

Ve a `Settings → Environments` y crea tres environments:

| Environment | Rama que lo activa | Requiere aprobación manual |
|-------------|-------------------|---------------------------|
| `dev`       | `feature/*`       | No                        |
| `uat`       | `uat`             | No                        |
| `production`| `main`            | **Sí** (al menos 1 reviewer) |

Esto permite que el pipeline de `main` espere aprobación explícita antes de deployar a producción.

---

## 3. Configuración de AWS — cuenta y permisos

### 3.1 Usuario IAM para el pipeline de CI/CD

Crea un usuario IAM dedicado solo para GitHub Actions. **Nunca uses credenciales de root ni de tu usuario personal.**

El archivo `iam-cicd-policy.json` ya existe en la raíz del repo. Desde ahí ejecuta:

```bash
# Crear usuario
aws iam create-user --user-name creditiq-cicd-bot

# Adjuntar política (el archivo ya está en la raíz del repo)
aws iam put-user-policy \
  --user-name creditiq-cicd-bot \
  --policy-name CreditIQDeployPolicy \
  --policy-document file://iam-cicd-policy.json

# Generar Access Keys (guárdalas — solo se muestran una vez)
aws iam create-access-key --user-name creditiq-cicd-bot
# Guarda: AccessKeyId y SecretAccessKey → van como GitHub Secrets
```

> **Windows (PowerShell):** los comandos `aws` funcionan igual. Reemplaza `\` por `` ` `` para continuación de línea, o pon cada argumento en una sola línea.

### 3.2 Bucket S3 para artefactos SAM

SAM necesita un bucket S3 para subir los paquetes `.zip` de Lambda durante el deploy.

```bash
# Crear el bucket de artefactos (una sola vez por región)
aws s3 mb s3://creditiq-sam-artifacts-us-east-1 --region us-east-1

# Habilitar versionado (permite rollback de paquetes)
aws s3api put-bucket-versioning \
  --bucket creditiq-sam-artifacts-us-east-1 \
  --versioning-configuration Status=Enabled

# Lifecycle: limpiar artefactos viejos después de 30 días
aws s3api put-bucket-lifecycle-configuration \
  --bucket creditiq-sam-artifacts-us-east-1 \
  --lifecycle-configuration '{
    "Rules": [{
      "ID": "delete-old-artifacts",
      "Status": "Enabled",
      "Filter": {"Prefix": ""},
      "NoncurrentVersionExpiration": {"NoncurrentDays": 30}
    }]
  }'
```

---

## 4. Secrets y variables de entorno

### 4.1 SSM Parameter Store (secrets de runtime)

Los Lambda functions leen los secrets desde SSM en tiempo de deploy (SAM los inyecta como variables de entorno). **Nunca pongas secrets directamente en `template.yaml`.**

```bash
# ANTHROPIC_API_KEY — necesario si LlmProvider = anthropic_api
aws ssm put-parameter \
  --name "/creditiq/dev/anthropic-api-key" \
  --value "sk-ant-api03-..." \
  --type "SecureString"

aws ssm put-parameter \
  --name "/creditiq/uat/anthropic-api-key" \
  --value "sk-ant-api03-..." \
  --type "SecureString"

aws ssm put-parameter \
  --name "/creditiq/prod/anthropic-api-key" \
  --value "sk-ant-api03-..." \
  --type "SecureString"

# Verificar
aws ssm get-parameter \
  --name "/creditiq/dev/anthropic-api-key" \
  --with-decryption \
  --query "Parameter.Value" --output text
```

### 4.2 GitHub Secrets y Variables

GitHub Actions tiene dos tipos de valores configurables. Ve a `Settings → Secrets and variables → Actions`:

**Secrets** (valores sensibles, nunca visibles en logs):

| Secret | Valor | Descripción |
|--------|-------|-------------|
| `AWS_ACCESS_KEY_ID` | `AKIA...` | Access Key del bot IAM |
| `AWS_SECRET_ACCESS_KEY` | `...` | Secret Key del bot IAM |
| `AWS_REGION` | `us-east-1` | Región de despliegue |
| `AWS_ACCOUNT_ID` | `123456789012` | ID de cuenta AWS |
| `SAM_ARTIFACTS_BUCKET` | `creditiq-sam-artifacts-us-east-1` | Bucket de artefactos SAM |
| `ANTHROPIC_API_KEY_DEV` | `sk-ant-...` | API Key de Anthropic para dev |
| `ANTHROPIC_API_KEY_PROD` | `sk-ant-...` | API Key de Anthropic para prod |

**Variables** (valores no sensibles, tab "Variables" en la misma pantalla):

| Variable | Valor | Descripción |
|----------|-------|-------------|
| `VITE_API_URL_DEV` | `https://XXXX.execute-api.us-east-1.amazonaws.com/dev` | URL del API para el build de dev |
| `VITE_API_URL_PROD` | `https://XXXX.execute-api.us-east-1.amazonaws.com/prod` | URL del API para el build de prod |

> **Importante:** Las Variables se crean en la pestaña **Variables** (no Secrets) de `Settings → Secrets and variables → Actions`. En el workflow se acceden con `${{ vars.NOMBRE }}` y los Secrets con `${{ secrets.NOMBRE }}`.
>
> Los valores de `VITE_API_URL_*` los obtienes de los Outputs del stack después del primer `sam deploy`. Puedes actualizar estas Variables en cualquier momento sin necesidad de recrear el pipeline.

### 4.3 Variables de entorno locales

Crea un archivo `.env` local en `iastronauts_creditiq_back/` (ya en `.gitignore`):

```bash
# iastronauts_creditiq_back/.env
LLM_PROVIDER=anthropic_api
ANTHROPIC_API_KEY=sk-ant-api03-...
LLM_MODEL=claude-sonnet-4-6
AWS_REGION=us-east-1
STAGE=dev
MAIN_BUCKET=iastronauts-creditiq-us-east-1-dev
AWS_ACCOUNT_ID=123456789012
WORKFLOW_ARN=arn:aws:states:us-east-1:123456789012:stateMachine:creditiq-analysis-workflow-dev
```

Y para el frontend:

```bash
# iastronauts_creditiq_front/.env.development
VITE_API_URL=https://XXXXXXXX.execute-api.us-east-1.amazonaws.com/dev
VITE_STAGE=dev
VITE_COGNITO_USER_POOL_ID=us-east-1_XXXXXXXXX
VITE_COGNITO_CLIENT_ID=XXXXXXXXXXXXXXXXXXXXXXXXXX
```

---

## 5. Configuración de Cognito

Cognito provee la autenticación JWT. El middleware del backend extrae `tenant_id` de los claims del token. Este paso es manual (una vez por ambiente).

### 5.1 Crear User Pool

```bash
# Crear User Pool con los atributos custom necesarios
POOL_ID=$(aws cognito-idp create-user-pool \
  --pool-name "creditiq-dev" \
  --auto-verified-attributes email \
  --username-attributes email \
  --schema \
    AttributeDataType=String,Name=tenant_id,Mutable=false,Required=false \
    AttributeDataType=String,Name=tenant_name,Mutable=true,Required=false \
    AttributeDataType=String,Name=tenant_tier,Mutable=true,Required=false \
    AttributeDataType=String,Name=permissions,Mutable=true,Required=false \
  --query "UserPool.Id" --output text)

echo "User Pool ID: $POOL_ID"
```

### 5.2 Crear App Client

```bash
CLIENT_ID=$(aws cognito-idp create-user-pool-client \
  --user-pool-id "$POOL_ID" \
  --client-name "creditiq-web-dev" \
  --no-generate-secret \
  --explicit-auth-flows \
    ALLOW_USER_PASSWORD_AUTH \
    ALLOW_USER_SRP_AUTH \
    ALLOW_REFRESH_TOKEN_AUTH \
  --query "UserPoolClient.ClientId" --output text)

echo "Client ID: $CLIENT_ID"
```

### 5.3 Lambda trigger — Pre Token Generation

Este trigger copia los atributos `custom:*` al JWT access token (necesario para que el backend los lea):

Crea `cognito_trigger/pre_token_generation.py`:

```python
def handler(event, context):
    """Copia custom attributes al access token como claims adicionales."""
    user_attrs = {
        attr["Name"]: attr["Value"]
        for attr in event["request"].get("userAttributes", {}).items()
        if isinstance(event["request"].get("userAttributes"), dict)
    }
    # Para versiones V2 del trigger
    event["response"]["claimsAndScopeOverrideDetails"] = {
        "accessTokenGeneration": {
            "claimsToAddOrOverride": {
                "tenant_id":   event["request"]["userAttributes"].get("custom:tenant_id", ""),
                "tenant_name": event["request"]["userAttributes"].get("custom:tenant_name", ""),
                "tenant_tier": event["request"]["userAttributes"].get("custom:tenant_tier", "professional"),
                "permissions": event["request"]["userAttributes"].get("custom:permissions", "analyses:create,analyses:read"),
            }
        }
    }
    return event
```

Registrar el trigger:

```bash
aws cognito-idp update-user-pool \
  --user-pool-id "$POOL_ID" \
  --lambda-config PreTokenGenerationConfig='{
    "LambdaVersion": "V2_0",
    "LambdaArn": "arn:aws:lambda:us-east-1:ACCOUNT_ID:function:creditiq-pre-token-gen-dev"
  }'
```

### 5.4 Crear tenant de demo

```bash
# Crear usuario
aws cognito-idp admin-create-user \
  --user-pool-id "$POOL_ID" \
  --username "demo@btgpactual.com" \
  --temporary-password "CreditIQ2026!" \
  --user-attributes \
    Name=email,Value=demo@btgpactual.com \
    Name=email_verified,Value=true \
    "Name=custom:tenant_id,Value=btg-demo-001" \
    "Name=custom:tenant_name,Value=BTG Pactual Demo" \
    "Name=custom:tenant_tier,Value=enterprise" \
    "Name=custom:permissions,Value=analyses:create,analyses:read"

# Confirmar usuario sin esperar cambio de contraseña (solo para demo)
aws cognito-idp admin-set-user-password \
  --user-pool-id "$POOL_ID" \
  --username "demo@btgpactual.com" \
  --password "CreditIQ2026!" \
  --permanent
```

### 5.5 Agregar autorizador al API Gateway

Una vez que tengas el `POOL_ID` y el `CLIENT_ID`, agrega a `template.yaml`:

```yaml
Parameters:
  CognitoUserPoolId:
    Type: String
    Default: ""
  CognitoClientId:
    Type: String
    Default: ""

# En CreditIQApi:
CreditIQApi:
  Type: AWS::Serverless::HttpApi
  Properties:
    StageName: !Ref Stage
    Auth:
      DefaultAuthorizer: CognitoAuth
      Authorizers:
        CognitoAuth:
          JwtConfiguration:
            issuer: !Sub "https://cognito-idp.${AWS::Region}.amazonaws.com/${CognitoUserPoolId}"
            audience:
              - !Ref CognitoClientId
          IdentitySource: "$request.header.Authorization"
```

> **Para el demo sin Cognito:** omite el bloque `Auth`. El middleware acepta `x-tenant-id` como header y `tenant_id` en el body como fallback. Esto es suficiente para la demo del concurso.

---

## 6. Build y deploy del backend (SAM)

### 6.1 Prerrequisitos locales

```bash
# Verificar instalaciones
aws --version          # aws-cli/2.x
sam --version          # SAM CLI, version 1.x
docker --version       # Docker 2x.x (necesario para arm64)
python --version       # Python 3.12.x
```

### 6.2 samconfig.toml

Crea `iastronauts_creditiq_back/samconfig.toml`. Este archivo **no se commitea** (está en `.gitignore`). Cada desarrollador tiene el suyo localmente; el pipeline usa flags de CLI.

```toml
version = 0.1

[default.global.parameters]
stack_name = "creditiq-dev"

[default.build.parameters]
cached = true
parallel = true

[default.deploy.parameters]
capabilities         = "CAPABILITY_IAM CAPABILITY_NAMED_IAM"
confirm_changeset    = true
resolve_s3           = true
s3_bucket            = "creditiq-sam-artifacts-us-east-1"
s3_prefix            = "creditiq-dev"
region               = "us-east-1"
parameter_overrides  = [
  "Stage=dev",
  "LlmProvider=anthropic_api",
  "AnthropicApiKey=REEMPLAZA_CON_TU_KEY"
]
```

### 6.3 Primera vez — deploy guiado

```bash
cd iastronauts_creditiq_back

# Build (usa container porque el template declara arm64)
# Docker Desktop debe estar corriendo
sam build --use-container

# Deploy interactivo — SAM pregunta cada parámetro
sam deploy --guided
```

Respuestas recomendadas al prompt interactivo:

```
Stack Name [sam-app]: creditiq-dev
AWS Region [us-east-1]: us-east-1
Parameter Stage [dev]: dev
Parameter LlmProvider [anthropic_api]: anthropic_api
Parameter AnthropicApiKey []: sk-ant-api03-TU-KEY
Confirm changes before deploy [y/N]: y
Allow SAM CLI IAM role creation [Y/n]: Y
Disable rollback [y/N]: N
Save arguments to configuration file [Y/n]: Y
SAM configuration file [samconfig.toml]: samconfig.toml
SAM configuration environment [default]: default
```

SAM muestra un changeset completo antes de crear cualquier recurso. Revisa y confirma con `y`.

**Tiempo esperado:** 8–12 minutos la primera vez (crea ~15 recursos en CloudFormation).

### 6.4 Outputs importantes

Al terminar el deploy, SAM muestra:

```
Key     ApiUrl
Value   https://XXXXXXXX.execute-api.us-east-1.amazonaws.com/dev

Key     CloudFrontUrl
Value   https://YYYYYYYY.cloudfront.net

Key     CloudFrontDistributionId
Value   EDFDVBD6EXAMPLE

Key     FrontendBucketName
Value   iastronauts-creditiq-frontend-us-east-1-dev

Key     MainBucketName
Value   iastronauts-creditiq-us-east-1-dev

Key     WorkflowArn
Value   arn:aws:states:us-east-1:ACCOUNT:stateMachine:creditiq-analysis-workflow-dev
```

Con estos valores debes hacer dos cosas antes de que el pipeline de CI/CD funcione de extremo a extremo:

1. **Configurar `VITE_API_URL_DEV`** en GitHub Variables con el valor de `ApiUrl`.
2. El pipeline de frontend usa `CloudFrontDistributionId` automáticamente vía `aws cloudformation describe-stacks` — no necesitas copiarlo manualmente.

También puedes recuperarlos después:

```bash
aws cloudformation describe-stacks \
  --stack-name creditiq-dev \
  --query "Stacks[0].Outputs" \
  --output table
```

### 6.5 Deploys posteriores

```bash
# Build + deploy sin confirmación (para iteraciones rápidas)
sam build --use-container && \
sam deploy --no-confirm-changeset

# Solo deploy (si no cambiaron dependencias Python)
sam deploy --no-confirm-changeset
```

### 6.6 Sin Docker — arquitectura x86_64

Si Docker no está disponible, cambia una línea en `template.yaml`:

```yaml
# Globals.Function — cambiar arm64 por x86_64
Architectures: [x86_64]
```

Luego:

```bash
# Build nativo (sin container)
sam build

# Deploy normal
sam deploy --no-confirm-changeset
```

> **Nota:** `arm64` (Graviton2) es ~20% más barato y rápido. Úsalo en producción. `x86_64` es más fácil de buildear localmente en Windows.

---

## 7. Build y deploy del frontend

### 7.1 Variables de entorno de producción

Crea `iastronauts_creditiq_front/.env.production` (no se commitea):

```bash
VITE_API_URL=https://XXXXXXXX.execute-api.us-east-1.amazonaws.com/dev
VITE_STAGE=dev
VITE_COGNITO_USER_POOL_ID=us-east-1_XXXXXXXXX
VITE_COGNITO_CLIENT_ID=XXXXXXXXXXXXXXXXXXXXXXXXXX
```

### 7.2 Build

```bash
cd iastronauts_creditiq_front

npm install
npm run build
# Genera: dist/ con index.html + assets/
```

### 7.3 Subir a S3

```bash
BUCKET="iastronauts-creditiq-frontend-us-east-1-dev"

# Assets estáticos — caché larga (tienen hash en el nombre)
aws s3 sync dist/ s3://$BUCKET/ \
  --delete \
  --exclude "index.html" \
  --cache-control "max-age=31536000,immutable"

# index.html — nunca cachear (es el entry point de la SPA)
aws s3 cp dist/index.html s3://$BUCKET/index.html \
  --cache-control "no-cache, no-store, must-revalidate" \
  --content-type "text/html; charset=utf-8"
```

### 7.4 Invalidar caché de CloudFront

```bash
DIST_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?contains(Origins.Items[0].DomainName, 'frontend')].Id" \
  --output text)

aws cloudfront create-invalidation \
  --distribution-id "$DIST_ID" \
  --paths "/*"

echo "Invalidación creada para distribución $DIST_ID"
```

---

## 8. Pipelines CI/CD con GitHub Actions

### 8.1 Pipeline Backend — `.github/workflows/backend.yml`

```yaml
name: Backend — Build & Deploy

on:
  push:
    branches: [main, uat]
    paths:
      - 'iastronauts_creditiq_back/**'
      - '.github/workflows/backend.yml'
  pull_request:
    branches: [main, uat]
    paths:
      - 'iastronauts_creditiq_back/**'

env:
  AWS_REGION: ${{ secrets.AWS_REGION }}
  SAM_ARTIFACTS_BUCKET: ${{ secrets.SAM_ARTIFACTS_BUCKET }}
  PYTHON_VERSION: "3.12"

jobs:
  # ─── TEST ──────────────────────────────────────────────
  test:
    name: Test
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: iastronauts_creditiq_back

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip
          cache-dependency-path: iastronauts_creditiq_back/src/requirements.txt

      - name: Instalar dependencias
        run: |
          pip install -r src/requirements.txt
          pip install pytest pytest-cov

      - name: Ejecutar tests
        run: pytest tests/ -v --tb=short
        env:
          LLM_PROVIDER: anthropic_api
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY_DEV }}
          AWS_REGION: ${{ env.AWS_REGION }}
          STAGE: test

  # ─── BUILD ─────────────────────────────────────────────
  build:
    name: SAM Build
    runs-on: ubuntu-latest
    needs: test
    defaults:
      run:
        working-directory: iastronauts_creditiq_back

    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/setup-sam@v2
        with:
          use-installer: true

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id:     ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region:            ${{ env.AWS_REGION }}

      - name: SAM Build
        run: sam build --use-container --parallel

      - name: Subir artefacto de build
        uses: actions/upload-artifact@v4
        with:
          name: sam-build-${{ github.sha }}
          path: iastronauts_creditiq_back/.aws-sam/build/
          retention-days: 1

  # ─── DEPLOY DEV ────────────────────────────────────────
  deploy-dev:
    name: Deploy → dev
    runs-on: ubuntu-latest
    needs: build
    if: github.ref == 'refs/heads/uat' || github.ref == 'refs/heads/main'
    environment: dev

    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/setup-sam@v2
        with:
          use-installer: true

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id:     ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region:            ${{ env.AWS_REGION }}

      - name: Descargar artefacto de build
        uses: actions/download-artifact@v4
        with:
          name: sam-build-${{ github.sha }}
          path: iastronauts_creditiq_back/.aws-sam/build/

      - name: Deploy → dev
        working-directory: iastronauts_creditiq_back
        run: |
          sam deploy \
            --stack-name creditiq-dev \
            --s3-bucket ${{ env.SAM_ARTIFACTS_BUCKET }} \
            --s3-prefix creditiq-dev \
            --region ${{ env.AWS_REGION }} \
            --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset \
            --parameter-overrides \
              Stage=dev \
              LlmProvider=anthropic_api \
              AnthropicApiKey=${{ secrets.ANTHROPIC_API_KEY_DEV }}

  # ─── DEPLOY PROD ───────────────────────────────────────
  deploy-prod:
    name: Deploy → production
    runs-on: ubuntu-latest
    needs: deploy-dev
    if: github.ref == 'refs/heads/main'
    environment: production   # requiere aprobación manual en GitHub

    steps:
      - uses: actions/checkout@v4

      - uses: aws-actions/setup-sam@v2
        with:
          use-installer: true

      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id:     ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region:            ${{ env.AWS_REGION }}

      - name: Descargar artefacto de build
        uses: actions/download-artifact@v4
        with:
          name: sam-build-${{ github.sha }}
          path: iastronauts_creditiq_back/.aws-sam/build/

      - name: Deploy → production
        working-directory: iastronauts_creditiq_back
        run: |
          sam deploy \
            --stack-name creditiq-prod \
            --s3-bucket ${{ env.SAM_ARTIFACTS_BUCKET }} \
            --s3-prefix creditiq-prod \
            --region ${{ env.AWS_REGION }} \
            --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset \
            --parameter-overrides \
              Stage=prod \
              LlmProvider=anthropic_api \
              AnthropicApiKey=${{ secrets.ANTHROPIC_API_KEY_PROD }}
```

### 8.2 Pipeline Frontend — `.github/workflows/frontend.yml`

```yaml
name: Frontend — Build & Deploy

on:
  push:
    branches: [main, uat]
    paths:
      - 'iastronauts_creditiq_front/**'
      - '.github/workflows/frontend.yml'
  pull_request:
    branches: [main, uat]
    paths:
      - 'iastronauts_creditiq_front/**'

env:
  AWS_REGION: ${{ secrets.AWS_REGION }}
  NODE_VERSION: "20"

jobs:
  # ─── BUILD ─────────────────────────────────────────────
  build:
    name: Build
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: iastronauts_creditiq_front

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: npm
          cache-dependency-path: iastronauts_creditiq_front/package-lock.json

      - name: Instalar dependencias
        run: npm ci

      - name: Type-check + lint
        run: |
          npm run lint
          npx tsc --noEmit

      - name: Build de producción
        run: npm run build
        env:
          VITE_API_URL:    ${{ vars.VITE_API_URL_DEV }}
          VITE_STAGE:      dev

      - name: Subir artefacto
        uses: actions/upload-artifact@v4
        with:
          name: frontend-dist-${{ github.sha }}
          path: iastronauts_creditiq_front/dist/
          retention-days: 1

  # ─── DEPLOY DEV ────────────────────────────────────────
  deploy-dev:
    name: Deploy → dev
    runs-on: ubuntu-latest
    needs: build
    if: github.ref == 'refs/heads/uat' || github.ref == 'refs/heads/main'
    environment: dev

    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id:     ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region:            ${{ env.AWS_REGION }}

      - name: Descargar artefacto
        uses: actions/download-artifact@v4
        with:
          name: frontend-dist-${{ github.sha }}
          path: dist/

      - name: Sync a S3
        run: |
          BUCKET="iastronauts-creditiq-frontend-us-east-1-dev"

          # Assets con hash — caché permanente
          aws s3 sync dist/ s3://$BUCKET/ \
            --delete \
            --exclude "index.html" \
            --cache-control "max-age=31536000,immutable"

          # index.html — sin caché
          aws s3 cp dist/index.html s3://$BUCKET/index.html \
            --cache-control "no-cache, no-store, must-revalidate" \
            --content-type "text/html; charset=utf-8"

      - name: Invalidar CloudFront
        run: |
          DIST_ID=$(aws cloudformation describe-stacks \
            --stack-name creditiq-dev \
            --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" \
            --output text)

          aws cloudfront create-invalidation \
            --distribution-id "$DIST_ID" \
            --paths "/*"

  # ─── DEPLOY PROD ───────────────────────────────────────
  deploy-prod:
    name: Deploy → production
    runs-on: ubuntu-latest
    needs: deploy-dev
    if: github.ref == 'refs/heads/main'
    environment: production

    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id:     ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region:            ${{ env.AWS_REGION }}

      - name: Rebuild con vars de producción
        uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: npm
          cache-dependency-path: iastronauts_creditiq_front/package-lock.json

      - name: Build prod
        working-directory: iastronauts_creditiq_front
        run: |
          npm ci
          npm run build
        env:
          VITE_API_URL: ${{ vars.VITE_API_URL_PROD }}
          VITE_STAGE:   prod

      - name: Sync a S3
        run: |
          BUCKET="iastronauts-creditiq-frontend-us-east-1-prod"

          aws s3 sync iastronauts_creditiq_front/dist/ s3://$BUCKET/ \
            --delete \
            --exclude "index.html" \
            --cache-control "max-age=31536000,immutable"

          aws s3 cp iastronauts_creditiq_front/dist/index.html \
            s3://$BUCKET/index.html \
            --cache-control "no-cache, no-store, must-revalidate" \
            --content-type "text/html; charset=utf-8"

      - name: Invalidar CloudFront prod
        run: |
          DIST_ID=$(aws cloudformation describe-stacks \
            --stack-name creditiq-prod \
            --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDistributionId'].OutputValue" \
            --output text)

          aws cloudfront create-invalidation \
            --distribution-id "$DIST_ID" \
            --paths "/*"
```

> **Nota:** Los pipelines usan `actions/download-artifact` para reutilizar el mismo artefacto de build sin recompilar. Esto garantiza que lo que se testeó es exactamente lo que se despliega.

---

## 9. Ambientes (dev / uat / prod)

Cada ambiente es un CloudFormation stack independiente con su propio set de recursos:

| Recurso | dev | uat | prod |
|---------|-----|-----|------|
| Stack name | `creditiq-dev` | `creditiq-uat` | `creditiq-prod` |
| S3 Bucket | `...us-east-1-dev` | `...us-east-1-uat` | `...us-east-1-prod` |
| Step Functions | `creditiq-workflow-dev` | `creditiq-workflow-uat` | `creditiq-workflow-prod` |
| Lambda timeout | Default SAM (30s) | Default | Aumentado si es necesario |
| LLM Model | `claude-sonnet-4-6` | `claude-sonnet-4-6` | `claude-sonnet-4-6` |

### Deploy manual a uat

```bash
cd iastronauts_creditiq_back

sam build --use-container

sam deploy \
  --stack-name creditiq-uat \
  --s3-bucket creditiq-sam-artifacts-us-east-1 \
  --s3-prefix creditiq-uat \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --no-confirm-changeset \
  --parameter-overrides \
    Stage=uat \
    LlmProvider=anthropic_api \
    "AnthropicApiKey=$(aws ssm get-parameter \
      --name /creditiq/uat/anthropic-api-key \
      --with-decryption \
      --query Parameter.Value \
      --output text)"
```

### Obtener outputs de cualquier ambiente

```bash
# Ver todos los outputs de un stack
aws cloudformation describe-stacks \
  --stack-name creditiq-uat \
  --query "Stacks[0].Outputs[*].[OutputKey, OutputValue]" \
  --output table
```

---

## 10. Smoke tests post-deploy

Ejecuta estos tests manualmente después de cada deploy importante. Reemplaza `$API` y `$TENANT` con tus valores reales.

```bash
API="https://XXXXXXXX.execute-api.us-east-1.amazonaws.com/dev"
TENANT="btg-demo-001"

echo "=== TEST 1: Presigned URL ==="
curl -s -X POST "$API/upload-url" \
  -H "Content-Type: application/json" \
  -H "x-tenant-id: $TENANT" \
  -d '{"file_name":"smoke-test.pdf","file_type":"pdf"}' | jq .
# Esperado: upload_url y s3_key

echo ""
echo "=== TEST 2: Lanzar análisis ==="
RESPONSE=$(curl -s -X POST "$API/analyses" \
  -H "Content-Type: application/json" \
  -H "x-tenant-id: $TENANT" \
  -d '{
    "files": [{"file_name":"test.pdf","s3_key":"uploads/btg-demo-001/uuid_test.pdf","file_type":"pdf"}],
    "company_name": "Empresa Prueba S.A.",
    "niif_standards": ["NIIF 7"],
    "report_language": "es"
  }')
echo "$RESPONSE" | jq .
JOB_ID=$(echo "$RESPONSE" | jq -r '.analysis_id')
echo "Job ID: $JOB_ID"

echo ""
echo "=== TEST 3: Estado del análisis ==="
curl -s "$API/analyses/$JOB_ID" \
  -H "x-tenant-id: $TENANT" | jq .
# Esperado: status "pending" o "processing"

echo ""
echo "=== TEST 4: Rechazo cross-tenant ==="
curl -s "$API/analyses/$JOB_ID" \
  -H "x-tenant-id: otro-tenant-999" | jq .
# Esperado: 403 Forbidden (cuando Cognito está activo)
# Sin Cognito: responde normalmente (el rechazo requiere JWT)

echo ""
echo "=== TEST 5: CloudWatch — verificar logs ==="
aws logs filter-log-events \
  --log-group-name "/aws/lambda/creditiq-orchestrator-dev" \
  --start-time $(date -d '5 minutes ago' +%s)000 \
  --filter-pattern "audit" \
  --query "events[*].message" \
  --output text | python3 -m json.tool
```

### Verificar el Step Functions pipeline

```bash
# Ver ejecuciones recientes
aws stepfunctions list-executions \
  --state-machine-arn "arn:aws:states:us-east-1:ACCOUNT:stateMachine:creditiq-analysis-workflow-dev" \
  --max-results 5

# Ver detalles de una ejecución específica
aws stepfunctions describe-execution \
  --execution-arn "arn:aws:states:us-east-1:ACCOUNT:execution:creditiq-analysis-workflow-dev:$JOB_ID"
```

---

## 11. Rollback

### Rollback de backend (CloudFormation)

CloudFormation mantiene el historial de changesets. Para volver a la versión anterior:

```bash
# Ver el historial de changesets
aws cloudformation list-change-sets \
  --stack-name creditiq-dev

# CloudFormation hace rollback automático si el deploy falla.
# Para rollback manual a la versión anterior:
aws cloudformation continue-update-rollback \
  --stack-name creditiq-dev

# Si el stack quedó en ROLLBACK_COMPLETE (solo aplica si fue el primer deploy):
aws cloudformation delete-stack --stack-name creditiq-dev
# Luego volver a deployar
```

### Rollback de frontend (S3 + CloudFront)

El bucket S3 tiene versionado habilitado. Para restaurar el `index.html` anterior:

```bash
BUCKET="iastronauts-creditiq-frontend-us-east-1-dev"

# Ver versiones de index.html
aws s3api list-object-versions \
  --bucket "$BUCKET" \
  --prefix "index.html" \
  --query "Versions[*].[VersionId, LastModified]" \
  --output table

# Restaurar versión específica
aws s3api copy-object \
  --bucket "$BUCKET" \
  --copy-source "$BUCKET/index.html?versionId=VERSION_ID_ANTERIOR" \
  --key "index.html" \
  --cache-control "no-cache, no-store, must-revalidate"

# Invalidar CloudFront
aws cloudfront create-invalidation \
  --distribution-id "$DIST_ID" \
  --paths "/*"
```

### Rollback de Lambda específica

```bash
# Ver versiones publicadas de una función
aws lambda list-versions-by-function \
  --function-name creditiq-orchestrator-dev \
  --query "Versions[*].[Version, LastModified]" \
  --output table

# SAM no publica versiones por defecto.
# El rollback real es hacer un nuevo deploy con el código anterior (git revert + push).
```

---

## 12. Referencia rápida de comandos

### Backend

```bash
cd iastronauts_creditiq_back

# Build local
sam build --use-container

# Deploy dev (primera vez)
sam deploy --guided

# Deploy dev (siguientes)
sam deploy --no-confirm-changeset

# Deploy con parámetros explícitos
sam deploy \
  --stack-name creditiq-dev \
  --s3-bucket creditiq-sam-artifacts-us-east-1 \
  --s3-prefix creditiq-dev \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --no-confirm-changeset \
  --parameter-overrides Stage=dev LlmProvider=anthropic_api AnthropicApiKey=sk-ant-...

# Ver logs en tiempo real de una Lambda
sam logs -n OrchestratorFunction --stack-name creditiq-dev --tail

# Invocar Lambda localmente (requiere Docker)
sam local invoke OrchestratorFunction \
  --event tests/events/sample_analysis.json \
  --env-vars .env.json

# Ver outputs del stack
aws cloudformation describe-stacks \
  --stack-name creditiq-dev \
  --query "Stacks[0].Outputs" \
  --output table

# Destruir todo el ambiente (irreversible)
aws cloudformation delete-stack --stack-name creditiq-dev
```

### Frontend

```bash
cd iastronauts_creditiq_front

npm install
npm run dev            # dev server en localhost:5173
npm run build          # build de producción → dist/
npm run lint           # ESLint
npm run preview        # preview del build

# Deploy manual
BUCKET="iastronauts-creditiq-frontend-us-east-1-dev"

aws s3 sync dist/ s3://$BUCKET/ \
  --delete \
  --exclude "index.html" \
  --cache-control "max-age=31536000,immutable"

aws s3 cp dist/index.html s3://$BUCKET/index.html \
  --cache-control "no-cache, no-store, must-revalidate"

aws cloudfront create-invalidation \
  --distribution-id DIST_ID \
  --paths "/*"
```

### Cognito y usuarios

```bash
# Obtener token JWT para pruebas
aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=demo@btgpactual.com,PASSWORD=CreditIQ2026! \
  --client-id CLIENT_ID \
  --query "AuthenticationResult.AccessToken" \
  --output text

# Usar el token en llamadas a la API (con Cognito activo)
TOKEN=$(aws cognito-idp initiate-auth \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME=demo@btgpactual.com,PASSWORD=CreditIQ2026! \
  --client-id CLIENT_ID \
  --query "AuthenticationResult.AccessToken" --output text)

curl -X POST "$API/analyses" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

### SSM — gestión de secrets

```bash
# Crear / actualizar
aws ssm put-parameter \
  --name "/creditiq/dev/anthropic-api-key" \
  --value "sk-ant-..." \
  --type "SecureString" \
  --overwrite

# Leer
aws ssm get-parameter \
  --name "/creditiq/dev/anthropic-api-key" \
  --with-decryption \
  --query "Parameter.Value" --output text

# Listar todos los parámetros del proyecto
aws ssm describe-parameters \
  --parameter-filters "Key=Name,Option=BeginsWith,Values=/creditiq/" \
  --query "Parameters[*].Name" --output table
```

---

## Checklist de primer despliegue

```
Pre-deploy:
  [ ] aws configure --profile creditiq-dev        verificado con sts get-caller-identity
  [ ] Docker Desktop corriendo                     docker ps sin error
  [ ] sam --version                                1.x o superior
  [ ] Bucket S3 de artefactos creado               creditiq-sam-artifacts-us-east-1
  [ ] SSM Parameter Store                          /creditiq/dev/anthropic-api-key creado

Backend:
  [ ] sam build --use-container                    Build Succeeded
  [ ] sam deploy --guided                          Stack: CREATE_COMPLETE
  [ ] Outputs anotados                             ApiUrl, CloudFrontUrl, MainBucketName

Frontend:
  [ ] .env.production con ApiUrl del backend       VITE_API_URL=...
  [ ] npm run build                                dist/ generado sin errores
  [ ] aws s3 sync                                  archivos en FrontendBucket
  [ ] CloudFront invalidation                      Invalidation creada

Post-deploy:
  [ ] curl POST /upload-url                        200 + upload_url
  [ ] curl POST /analyses                          202 + analysis_id
  [ ] curl GET /analyses/{id}                      200 + status
  [ ] Step Functions consola                       ejecución visible en AWS Console
  [ ] CloudWatch logs                              eventos de audit visibles

GitHub Actions:
  [ ] Secrets configurados en repo                 AWS_ACCESS_KEY_ID, etc.
  [ ] Environments creados                         dev, uat, production
  [ ] Branch protection en main y uat              Status checks obligatorios
  [ ] Push a uat                                   Pipeline verde end-to-end
```
