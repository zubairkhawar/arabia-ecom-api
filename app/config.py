from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    jwt_secret: str
    jwt_expires_minutes: int = 60 * 24 * 7
    fernet_key: str

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    meta_graph_version: str = "v21.0"

    app_env: str = "dev"
    app_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "https://arabia-ecom.vercel.app"
    link_domain: str = "https://arabia-ecom.vercel.app"

    wa_verify_token: str = "change-me"

    # Sole admin account. Only this email may sign into the admin portal.
    # Resellers signing up with this email are rejected.
    admin_email: str = "arabiadropshipping05@gmail.com"
    admin_password: str = "admin-change-me"


settings = Settings()
