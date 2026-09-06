from pydantic import BaseModel

from app.services.branding_settings_service import (
    DEFAULT_ICON_URL,
    DEFAULT_LOGIN_SUBTITLE,
    DEFAULT_PRODUCT_NAME,
    DEFAULT_AGENT_NAME,
)


class BrandingSettings(BaseModel):
    enabled: bool = False
    product_name: str = DEFAULT_PRODUCT_NAME
    login_subtitle: str = DEFAULT_LOGIN_SUBTITLE
    icon_url: str = DEFAULT_ICON_URL
    hide_login_sso: bool = False
    hide_version_link: bool = False
    contact_markdown: str = ""
    copyright_text: str = ""
    default_agent_name: str = DEFAULT_AGENT_NAME


class BrandingSettingsUpdate(BrandingSettings):
    pass


class PublicBrandingResponse(BaseModel):
    enabled: bool = False
    product_name: str
    login_subtitle: str
    icon_url: str
    hide_login_sso: bool = False
    hide_version_link: bool = False
    contact_markdown: str = ""
    copyright_text: str = ""
    default_agent_name: str
