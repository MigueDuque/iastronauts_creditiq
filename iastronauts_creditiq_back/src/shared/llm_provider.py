import os
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger()
logger.setLevel(logging.INFO)

class LLMProvider:
    """
    Servicio de conexión a modelos de lenguaje.
    Soporta:
    - API Directa de Anthropic (usando ANTHROPIC_API_KEY)
    - AWS Bedrock (usando roles de IAM y boto3)
    """
    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "anthropic_api").lower()
        self.model = os.getenv("LLM_MODEL", "claude-3-5-sonnet-20240620")
        
        logger.info(f"Inicializando LLMProvider con proveedor: {self.provider}")

        if self.provider == "anthropic_api":
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY no está configurada")
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
            
            # Bedrock usa un formato de modelo distinto. Usamos 'us.' para Cross-Region Inference Profiles
            self.model = os.getenv("BEDROCK_MODEL", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")
        else:
            raise ValueError(f"Proveedor LLM no soportado: {self.provider}")

    def generate_text(self, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> str:
        """
        Genera una respuesta en texto plano (Markdown, reportes, etc).
        """
        if self.provider == "anthropic_api":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            return response.content[0].text
            
        elif self.provider == "bedrock":
            # Usando la nueva API "Converse" de Bedrock (más moderna y estandarizada)
            response = self.client.converse(
                modelId=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": user_prompt}]
                    }
                ],
                system=[{"text": system_prompt}],
                inferenceConfig={
                    "maxTokens": 4096,
                    "temperature": temperature
                }
            )
            return response['output']['message']['content'][0]['text']

    def generate_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> Dict[str, Any]:
        """
        Genera una respuesta estructurada en formato JSON.
        Ideal para agentes extractores y analistas.
        """
        # Instrucción estricta para Claude de que retorne SOLO JSON
        json_system_prompt = f"{system_prompt}\n\nIMPORTANT: You must output ONLY valid JSON. Do not include markdown blocks like ```json. Just raw JSON."
        
        # Temperatura baja para mayor consistencia en el JSON
        text_response = self.generate_text(json_system_prompt, user_prompt, temperature)
        
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
            raise Exception("El modelo no devolvió un JSON válido.")