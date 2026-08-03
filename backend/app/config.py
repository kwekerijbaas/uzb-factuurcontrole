from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/uzk"

    # Azure App Service Authentication ("Easy Auth") zet de identiteit in
    # request-headers. Lokaal staat dat niet aan; dan wordt `dev_gebruiker`
    # gebruikt. In Azure moet `auth_vereist` aan blijven staan.
    auth_vereist: bool = True
    dev_gebruiker: str = "lokale-ontwikkelaar"

    # Waar geüploade bronbestanden en gegenereerde overzichten landen.
    opslag_pad: str = "./data"

    # Wettelijk minimumloon per uur, voor de controle bij een loontabel-upload.
    minimumloon: str = "14.40"


settings = Settings()
