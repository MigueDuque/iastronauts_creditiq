import os
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Above this output budget the Anthropic SDK rejects non-streaming requests
# (potential >10 min runtime), so generate_text switches to streaming.
_ANTHROPIC_NONSTREAM_MAX_TOKENS = 16384


class LLMError(Exception):
    """Base class for LLMProvider failures."""


class LLMTruncationError(LLMError):
    """Model hit max_tokens — output is incomplete. NOT retryable (a retry with the
    same budget reproduces the failure and re-bills the full call). Surface it so the
    caller can raise max_tokens or shrink the input."""


class LLMTransientError(LLMError):
    """A transient API error (rate limit / 5xx / overloaded / connection / timeout).
    Retryable. The Anthropic SDK already auto-retries these a few times internally;
    this lets a caller's own retry layer scope retries to *only* transient failures."""


class LLMInvalidJSONError(LLMError):
    """Model returned text that did not parse as JSON. Not retryable in the text path;
    use force_tool_json=True to make the model return a guaranteed-valid dict instead."""


def _log_cache_usage(message, model: str, job_id: Optional[str]) -> None:
    """Emit prompt-cache hit/write/uncached token counts so cache effectiveness is
    auditable in CloudWatch. cache_read≈0.1x, cache_write≈1.25x, input=full price.
    A persistent cache_read=0 across calls means a silent invalidator broke the prefix."""
    try:
        u = message.usage
        logger.info(
            "llm_cache | model=%s job=%s cache_read=%s cache_write=%s input=%s output=%s",
            model, job_id or "N/A",
            getattr(u, "cache_read_input_tokens", None),
            getattr(u, "cache_creation_input_tokens", None),
            getattr(u, "input_tokens", None),
            getattr(u, "output_tokens", None),
        )
    except Exception:
        pass

_TENANT_BOUNDARY_TEMPLATE = (
    "\n\n=== TENANT CONTEXT BOUNDARY ===\n"
    "Tenant: {tenant_id} | Analysis: {job_id}\n"
    "You are operating EXCLUSIVELY within this tenant's financial data context. "
    "Never reference, infer, or include data from other analyses, tenants, or "
    "any prior conversation context. "
    "All outputs must be scoped strictly to the financial data provided in this request.\n"
    "=== END BOUNDARY ===\n\n"
)

# Human-readable names for the supported output languages.
_LANGUAGE_NAMES = {
    "es": "Spanish (español)",
    "en": "English",
    "pt": "Portuguese (português)",
}

# Injected into every system prompt so the *prose* presented to the client is
# localized, while the machine-readable contract (JSON keys, enums, IDs) stays in
# English exactly as the prompts specify — those values are validated downstream.
_LANGUAGE_DIRECTIVE_TEMPLATE = (
    "\n\n=== OUTPUT LANGUAGE ===\n"
    "Write every human-readable, narrative or explanatory string VALUE in {language}: "
    "summaries, insights, findings, recommendations, headlines, theses, warnings and any prose "
    "intended for the end client.\n"
    "Do NOT translate the machine-readable contract. Keep ALL of the following exactly as "
    "specified, in English/code form: JSON keys and field names; enum values such as "
    "LOW / MEDIUM / HIGH, STABLE / GROWING / DECLINING / CRITICAL, markdown / pdf; status codes; "
    "account IDs; currency codes; and numeric values.\n"
    "Only the prose shown to the client is translated — the data structure remains unchanged.\n"
    "=== END OUTPUT LANGUAGE ===\n"
)


# Resolved once per warm container — avoids a Secrets Manager round-trip on every
# LLMProvider instantiation (one per agent invocation).
_cached_anthropic_key: Optional[str] = None


def _resolve_anthropic_key() -> str:
    """
    Resolve the Anthropic API key.

    Priority:
      1. ANTHROPIC_API_KEY env var       ← local dev (.env) / explicit override
      2. ANTHROPIC_API_KEY_SECRET_ARN    ← production (Secrets Manager)

    Cached for the container lifetime. Raises ValueError if neither source yields
    a key. WHY: keeping the key out of plaintext Lambda env vars (readable via the
    console / lambda:GetFunctionConfiguration) is a baseline requirement for a
    financial product.
    """
    global _cached_anthropic_key
    if _cached_anthropic_key:
        return _cached_anthropic_key

    env_key = os.getenv("ANTHROPIC_API_KEY")
    if env_key:
        _cached_anthropic_key = env_key
        return env_key

    secret_arn = os.getenv("ANTHROPIC_API_KEY_SECRET_ARN")
    if secret_arn:
        import boto3
        try:
            sm = boto3.client("secretsmanager")
            secret = sm.get_secret_value(SecretId=secret_arn).get("SecretString", "")
        except Exception as exc:
            raise ValueError(
                f"No se pudo obtener la API key desde Secrets Manager ({secret_arn}): {exc}"
            ) from exc
        # Support both a raw-string secret and a JSON {"ANTHROPIC_API_KEY": "..."} secret.
        key = secret
        try:
            parsed = json.loads(secret)
            if isinstance(parsed, dict):
                key = parsed.get("ANTHROPIC_API_KEY") or parsed.get("api_key") or ""
        except (json.JSONDecodeError, TypeError):
            pass
        if key:
            _cached_anthropic_key = key
            return key

    raise ValueError(
        "ANTHROPIC_API_KEY no está configurada (ni env var ni Secrets Manager)"
    )


class LLMProvider:
    """
    Servicio de conexión a modelos de lenguaje.
    Soporta:
    - API Directa de Anthropic (usando ANTHROPIC_API_KEY)
    - AWS Bedrock (usando roles de IAM y boto3)
    """
    # Class-level default so instances built without __init__ (tests stub the
    # constructor) still have a valid empty role context.
    _role_context_block: str = ""

    def __init__(self, model: Optional[str] = None):
        self.provider = os.getenv("LLM_PROVIDER", "anthropic_api").lower()
        # Per-agent override: callers can pass model=... (e.g. a cheaper model for
        # the mechanical extraction agent) to avoid paying premium rates on every
        # call. Falls back to the global LLM_MODEL env when not specified.
        self.model = model or os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")
        # Language for client-facing prose. Defaults to Spanish (es) — the clients
        # are Spanish-speaking even though the data contract stays in English.
        self.report_language = os.getenv("REPORT_LANGUAGE", "es").lower()

        # AI Analysis Perspectives — optional role-context block appended to every
        # system prompt of this provider instance (set once per job by the agent
        # handler via set_role_context). Empty string = no perspective (general).
        self._role_context_block: str = ""

        logger.info(f"Inicializando LLMProvider con proveedor: {self.provider}")

        if self.provider == "anthropic_api":
            import anthropic
            api_key = _resolve_anthropic_key()
            self.client = anthropic.Anthropic(api_key=api_key)
            
        elif self.provider == "bedrock":
            import boto3
            
            # Cargar credenciales explícitas si existen, sino boto3 usará el entorno por defecto
            aws_access_key = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY")
            aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
            aws_region = os.getenv("AWS_REGION", "us-east-1")
            
            client_kwargs = {'service_name': 'bedrock-runtime', 'region_name': aws_region}
            if aws_access_key and aws_secret_key:
                client_kwargs['aws_access_key_id'] = aws_access_key
                client_kwargs['aws_secret_access_key'] = aws_secret_key
                
            self.client = boto3.client(**client_kwargs)
            
            # Bedrock usa un formato de modelo distinto. Usamos 'us.' para Cross-Region Inference Profiles.
            # Un model explícito (override por agente) tiene prioridad sobre BEDROCK_MODEL.
            self.model = model or os.getenv("BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001")
        else:
            raise ValueError(f"Proveedor LLM no soportado: {self.provider}")

    def set_role_context(self, role_block: str) -> None:
        """
        Register the analysis-role perspective block (from
        shared.role_context.build_role_prompt_block) so EVERY subsequent LLM call
        made through this provider instance carries the user's professional
        perspective — no per-call plumbing required in agent code.
        """
        self._role_context_block = role_block or ""

    def _inject_tenant_boundary(
        self,
        system_prompt: str,
        tenant_id: Optional[str],
        job_id: Optional[str],
    ) -> str:
        """
        Appends a tenant isolation boundary to the system prompt.

        WHY: LLMs can retain implicit context across calls within the same
        process (warm Lambda container). Explicitly scoping the system prompt
        to a tenant + job prevents cross-tenant context leakage and gives a
        clear audit anchor in model outputs.
        """
        if not tenant_id:
            return system_prompt
        boundary = _TENANT_BOUNDARY_TEMPLATE.format(
            tenant_id=tenant_id,
            job_id=job_id or "N/A",
        )
        return system_prompt + boundary

    def _inject_language(self, system_prompt: str) -> str:
        """
        Append the output-language directive so client-facing prose is localized
        while the JSON contract (keys, enums, IDs) stays in English. English is a
        no-op since the prompts are already authored in English.
        """
        if self.report_language == "en":
            return system_prompt
        language = _LANGUAGE_NAMES.get(self.report_language, _LANGUAGE_NAMES["es"])
        return system_prompt + _LANGUAGE_DIRECTIVE_TEMPLATE.format(language=language)

    def _dynamic_suffix(self, tenant_id: Optional[str], job_id: Optional[str]) -> str:
        """The per-call/per-tenant tail of the system prompt: tenant boundary + language
        directive. Kept SEPARATE from the static prompt so the static prefix stays a
        stable prompt-cache key across calls, tenants, and retries."""
        suffix = ""
        if tenant_id:
            suffix += _TENANT_BOUNDARY_TEMPLATE.format(tenant_id=tenant_id, job_id=job_id or "N/A")
        if self.report_language != "en":
            language = _LANGUAGE_NAMES.get(self.report_language, _LANGUAGE_NAMES["es"])
            suffix += _LANGUAGE_DIRECTIVE_TEMPLATE.format(language=language)
        # Role perspective is per-job (volatile) — it lives in the uncached tail so
        # changing roles between analyses never invalidates the cached static prefix.
        if self._role_context_block:
            suffix += self._role_context_block
        return suffix

    def _anthropic_system_blocks(
        self, system_prompt: str, tenant_id: Optional[str], job_id: Optional[str]
    ) -> list:
        """Build the Anthropic `system` as content blocks with prompt caching.

        The large static prompt goes first with `cache_control: ephemeral` — identical
        bytes across every call, so within the 5-min TTL repeated calls (per-file loops,
        retries, back-to-back analyses) read it at ~10% of input price instead of paying
        full price each time. The volatile tenant/language tail is a separate, uncached
        trailing block so it never invalidates the cached prefix.

        Caching is silently a no-op when the prefix is below the model's minimum
        cacheable size (4096 tokens for Haiku 4.5) — no error, just no benefit.
        """
        blocks = [{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }]
        suffix = self._dynamic_suffix(tenant_id, job_id)
        if suffix:
            blocks.append({"type": "text", "text": suffix})
        return blocks

    def _wrap_api_error(self, exc: Exception) -> Exception:
        """Map an anthropic SDK exception to LLMTransientError when it's retryable,
        otherwise return it unchanged."""
        try:
            import anthropic
        except Exception:
            return exc
        transient = (
            anthropic.RateLimitError,
            anthropic.InternalServerError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
        )
        overloaded = getattr(anthropic, "OverloadedError", None)
        if overloaded is not None:
            transient = transient + (overloaded,)
        if isinstance(exc, transient):
            return LLMTransientError(str(exc))
        return exc

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        tenant_id: Optional[str] = None,
        job_id: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        """
        Genera una respuesta en texto plano (Markdown, reportes, etc).

        tenant_id / job_id: when provided, a tenant boundary is injected into
        the system prompt to prevent cross-tenant AI context contamination.
        max_tokens: override output token limit (default 4096; use up to 16384
        for extraction calls that return large JSON arrays).
        """
        logger.info(
            "llm_call | provider=%s model=%s tenant=%s job=%s lang=%s max_tokens=%d",
            self.provider, self.model, tenant_id or "anon", job_id or "N/A",
            self.report_language, max_tokens,
        )
        if self.provider == "anthropic_api":
            system_blocks = self._anthropic_system_blocks(system_prompt, tenant_id, job_id)
            try:
                # The SDK rejects non-streaming requests whose max_tokens could take
                # >10 min; stream large-output calls (e.g. extraction JSON) to avoid it.
                if max_tokens > _ANTHROPIC_NONSTREAM_MAX_TOKENS:
                    with self.client.messages.stream(
                        model=self.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system_blocks,
                        messages=[{"role": "user", "content": user_prompt}],
                    ) as stream:
                        final = stream.get_final_message()
                else:
                    final = self.client.messages.create(
                        model=self.model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system_blocks,
                        messages=[{"role": "user", "content": user_prompt}],
                    )
            except Exception as exc:
                raise self._wrap_api_error(exc) from exc
            _log_cache_usage(final, self.model, job_id)
            if final.stop_reason == "max_tokens":
                raise LLMTruncationError(
                    f"Respuesta truncada: el modelo alcanzó el límite de "
                    f"max_tokens={max_tokens} (output incompleto). "
                    "Aumente max_tokens o reduzca el tamaño de la entrada."
                )
            return final.content[0].text

        elif self.provider == "bedrock":
            scoped_system = self._inject_tenant_boundary(system_prompt, tenant_id, job_id)
            scoped_system = self._inject_language(scoped_system)
            scoped_system += self._role_context_block
            # Usando la nueva API "Converse" de Bedrock (más moderna y estandarizada)
            response = self.client.converse(
                modelId=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": user_prompt}]
                    }
                ],
                system=[{"text": scoped_system}],
                inferenceConfig={
                    "maxTokens": max_tokens,
                    "temperature": temperature
                }
            )
            if response.get("stopReason") == "max_tokens":
                raise LLMTruncationError(
                    f"Respuesta truncada: el modelo alcanzó el límite de "
                    f"max_tokens={max_tokens} (output incompleto). "
                    "Aumente max_tokens o reduzca el tamaño de la entrada."
                )
            return response['output']['message']['content'][0]['text']

    def generate_chat(
        self,
        system_prompt: str,
        messages: list,
        temperature: float = 0.7,
        tenant_id: Optional[str] = None,
        job_id: Optional[str] = None,
        max_tokens: int = 2048,
    ) -> str:
        """Multi-turn conversation. `messages` is a list of {"role": "user"/"assistant", "content": "..."} dicts."""
        scoped_system = self._inject_tenant_boundary(system_prompt, tenant_id, job_id)
        scoped_system = self._inject_language(scoped_system)
        scoped_system += self._role_context_block
        logger.info(
            "llm_chat | provider=%s model=%s tenant=%s job=%s turns=%d",
            self.provider, self.model, tenant_id or "anon", job_id or "N/A", len(messages),
        )
        if self.provider == "anthropic_api":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=scoped_system,
                messages=messages,
            )
            return response.content[0].text

        elif self.provider == "bedrock":
            bedrock_messages = [
                {"role": m["role"], "content": [{"text": m["content"]}]}
                for m in messages
            ]
            response = self.client.converse(
                modelId=self.model,
                messages=bedrock_messages,
                system=[{"text": scoped_system}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            )
            return response["output"]["message"]["content"][0]["text"]

        return ""

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        tenant_id: Optional[str] = None,
        job_id: Optional[str] = None,
        max_tokens: int = 4096,
        force_tool_json: bool = False,
    ) -> Dict[str, Any]:
        """
        Genera una respuesta estructurada en formato JSON.
        Ideal para agentes extractores y analistas.

        force_tool_json (anthropic_api only): force the model to answer through a single
        tool call, so the SDK returns an already-parsed dict. This eliminates the whole
        class of JSON-parse failures (and the wasteful retries they triggered) — the API
        guarantees the tool input is valid JSON. The system prompt still drives the
        content; the tool is a permissive object so the structure isn't over-constrained.
        Falls back to the text path on bedrock.
        """
        if force_tool_json and self.provider == "anthropic_api":
            return self._generate_json_via_tool(
                system_prompt, user_prompt, temperature,
                tenant_id=tenant_id, job_id=job_id, max_tokens=max_tokens,
            )

        # Instrucción estricta para Claude de que retorne SOLO JSON
        json_system_prompt = f"{system_prompt}\n\nIMPORTANT: You must output ONLY valid JSON. Do not include markdown blocks like ```json. Just raw JSON."

        # Temperatura baja para mayor consistencia en el JSON
        text_response = self.generate_text(
            json_system_prompt, user_prompt, temperature,
            tenant_id=tenant_id, job_id=job_id, max_tokens=max_tokens,
        )

        try:
            # Limpiar posible markdown si el modelo lo agrega por error
            cleaned = text_response.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]

            return json.loads(cleaned.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Error parseando JSON del modelo: {e}")
            logger.error(f"Respuesta original: {text_response}")
            raise LLMInvalidJSONError("El modelo no devolvió un JSON válido.")

    # Single forced tool: the model must answer by calling it, and the SDK hands us
    # `block.input` already parsed into a dict. Permissive schema (the prompt defines
    # the real shape) to avoid changing extraction content vs the free-text JSON path.
    _JSON_TOOL_NAME = "emit_result"

    def _generate_json_via_tool(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        tenant_id: Optional[str],
        job_id: Optional[str],
        max_tokens: int,
    ) -> Dict[str, Any]:
        system_blocks = self._anthropic_system_blocks(system_prompt, tenant_id, job_id)
        tool = {
            "name": self._JSON_TOOL_NAME,
            "description": "Return the structured result as a JSON object following the "
                           "schema described in the system prompt.",
            "input_schema": {"type": "object", "additionalProperties": True},
        }
        logger.info(
            "llm_json_tool | model=%s tenant=%s job=%s max_tokens=%d",
            self.model, tenant_id or "anon", job_id or "N/A", max_tokens,
        )
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_blocks,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": self._JSON_TOOL_NAME},
        )
        try:
            if max_tokens > _ANTHROPIC_NONSTREAM_MAX_TOKENS:
                with self.client.messages.stream(**kwargs) as stream:
                    final = stream.get_final_message()
            else:
                final = self.client.messages.create(**kwargs)
        except Exception as exc:
            raise self._wrap_api_error(exc) from exc

        _log_cache_usage(final, self.model, job_id)
        if final.stop_reason == "max_tokens":
            raise LLMTruncationError(
                f"Respuesta truncada: el modelo alcanzó max_tokens={max_tokens} "
                "antes de completar el tool call (output incompleto). "
                "Aumente max_tokens o reduzca el tamaño de la entrada."
            )
        for block in final.content:
            if getattr(block, "type", None) == "tool_use" and block.name == self._JSON_TOOL_NAME:
                return block.input if isinstance(block.input, dict) else {}
        raise LLMInvalidJSONError("El modelo no devolvió un tool_use con el resultado JSON.")