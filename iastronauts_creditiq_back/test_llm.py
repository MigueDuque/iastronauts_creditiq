import os
import sys

# Agregamos el directorio actual al PYTHONPATH para que pueda importar 'src'
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

# Intentamos cargar variables desde .env si está disponible
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Archivo .env cargado.")
except ImportError:
    print("⚠️ python-dotenv no instalado. Asegúrate de tener las variables de entorno configuradas.")

from src.shared.llm_provider import LLMProvider

def main():
    provider_name = os.getenv('LLM_PROVIDER', 'ANTHROPIC_API_KEY')
    print(f"🚀 Inicializando prueba con proveedor: {provider_name.upper()}\n")
    
    try:
        provider = LLMProvider()
    except Exception as e:
        print(f"❌ Error inicializando el proveedor: {e}")
        return

    print("-" * 50)
    print("1. PROBANDO generate_text()")
    print("-" * 50)
    try:
        text_response = provider.generate_text(
            system_prompt="Eres un asistente financiero muy conciso.",
            user_prompt="Define qué es el EBITDA en una sola oración muy breve."
        )
        print("✅ Respuesta recibida:\n")
        print(text_response)
    except Exception as e:
        print(f"❌ Error en generate_text: {e}")

    print("\n" + "-" * 50)
    print("2. PROBANDO generate_json()")
    print("-" * 50)
    try:
        json_response = provider.generate_json(
            system_prompt="Eres un extractor financiero experto.",
            user_prompt="Analiza la siguiente frase y extrae los datos: 'Tesla Motors reportó un EBITDA de 1500 millones en el año 2023.' Mapea las llaves 'company', 'ebitda', 'currency', 'year'."
        )
        print(f"✅ Respuesta recibida (Tipo de dato de Python: {type(json_response)}):\n")
        
        import json
        print(json.dumps(json_response, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"❌ Error en generate_json: {e}")

if __name__ == "__main__":
    main()
