from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import ValidationError


class Settings(BaseSettings):
    anthropic_api_key: str
    supabase_url: str
    supabase_service_role_key: str
    meta_wa_token: str = ""
    meta_wa_phone_number_id: str = ""
    meta_verify_token: str = ""
    meta_ig_access_token: str = ""
    meta_ig_page_id: str = ""
    asaas_api_key: str = ""
    asaas_base_url: str = "https://api.asaas.com/v3"
    # Transcrição de nota de voz (Groq / Whisper). Opcional de propósito: sem a chave
    # o agente volta a só avisar que não processa áudio, sem quebrar nada.
    groq_api_key: str = ""
    admin_api_key: str = ""
    whatsapp_app_secret: str = ""
    port: int = 8000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


try:
    settings = Settings()
except ValidationError as e:
    missing = [err["loc"][0] for err in e.errors() if err["type"] == "missing"]
    raise RuntimeError(
        f"Missing required environment variables: {missing}. "
        "Check your .env file or Railway environment settings."
    ) from e
