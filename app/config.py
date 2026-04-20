from pydantic_settings import BaseSettings


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
    admin_api_key: str = ""
    whatsapp_app_secret: str = ""
    port: int = 8000

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
