"""Constants for Hermes Conversation."""

from typing import Final

DOMAIN: Final = "hermes_conversation"
PLATFORMS: Final = ["conversation"]

CONF_API_URL: Final = "api_url"
CONF_API_KEY: Final = "api_key"
CONF_MODEL: Final = "model"
CONF_TIMEOUT: Final = "timeout"
CONF_CONTINUE_MODE: Final = "continue_mode"

DEFAULT_API_URL: Final = "http://host.docker.internal:8642"
DEFAULT_MODEL: Final = "hermes-agent"
DEFAULT_TIMEOUT: Final = 180
DEFAULT_SESSION_TTL_MINUTES: Final = 10
DEFAULT_PINNED_SESSION_TTL_HOURS: Final = 2
SESSION_STORE_VERSION: Final = 1

CONTINUE_AUTO: Final = "auto"
CONTINUE_ALWAYS: Final = "always"
CONTINUE_NEVER: Final = "never"
CONTINUE_MODES: Final = [CONTINUE_AUTO, CONTINUE_ALWAYS, CONTINUE_NEVER]
