from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/uzk"

    # --- Toegang ---------------------------------------------------------- #
    # Inloggen gaat via Supabase Auth (inlogcode per e-mail). Zet
    # `auth_vereist` alleen lokaal uit; anders is de app zonder login open.
    auth_vereist: bool = True
    dev_gebruiker: str = "lokaal@kwekerijbaas.nl"

    supabase_url: str = ""
    supabase_anon_key: str = ""

    # Ondertekent het sessiecookie. In productie verplicht een eigen waarde.
    sessie_geheim: str = "onveilig-standaard-geheim-alleen-voor-lokaal"
    cookie_secure: bool = True

    # Wie mag inloggen: alle adressen binnen deze domeinen, plus losse adressen.
    toegestane_domeinen: list[str] = ["kwekerijbaas.nl"]
    toegestane_emails: list[str] = []
    # Vertrokken medewerkers: deze adressen zijn geblokkeerd ook al vallen ze
    # binnen het domein. Werkt direct, ook voor een nog lopend sessiecookie.
    geblokkeerde_emails: list[str] = []

    # Waar geüploade bronbestanden en gegenereerde overzichten landen.
    opslag_pad: str = "./data"

    # Wettelijk minimumloon per uur, voor de controle bij een loontabel-upload.
    minimumloon: str = "14.40"

    # Hoe lang verwerkte weken bewaard blijven. Oudere weken worden opgeruimd
    # zodra er een nieuwe week wordt verwerkt.
    bewaartermijn_jaren: int = 2


settings = Settings()
