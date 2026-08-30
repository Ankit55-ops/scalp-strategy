"""Request/response schemas for provider connections (Exness via MT5)."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator

VALID_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


class ConnectionStatus(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIGURED = "CONFIGURED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    UNSUPPORTED_ACCOUNT = "UNSUPPORTED_ACCOUNT"
    RATE_LIMITED = "RATE_LIMITED"
    MAINTENANCE = "MAINTENANCE"
    ERROR = "ERROR"


class ConnectionMode(str, Enum):
    MT5_GATEWAY_AGENT = "mt5_gateway_agent"
    SERVER_SIDE_MT5 = "server_side_mt5"
    APPROVED_BRIDGE = "approved_bridge"


class ExnessTestGatewayRequest(BaseModel):
    """Test a secure MT5 Gateway Agent connection (no broker credentials)."""

    gateway_url: str = Field(min_length=8, max_length=512)
    pairing_code: str = Field(min_length=8, max_length=256)
    device_name: str = Field(min_length=1, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=128)


class ExnessTestServerSideRequest(BaseModel):
    """Test a secure server-side MT5 connector (broker credentials on server)."""

    login: str = Field(min_length=1, max_length=32)
    server: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    environment: str = "demo"
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("login")
    @classmethod
    def login_is_numeric(cls, v: str) -> str:
        if not v.strip().isdigit():
            raise ValueError("MT5 login must be numeric")
        return v.strip()


class ExnessConnectRequest(BaseModel):
    """Create/update an Exness connection with encrypted-at-rest credentials."""

    connection_mode: ConnectionMode
    display_name: str = Field(min_length=1, max_length=128)
    environment: str = "demo"
    # server-side connector fields (only submitted for that mode)
    login: str | None = Field(default=None, max_length=32)
    server: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    use_read_only: bool = True
    # gateway fields (only submitted for that mode)
    gateway_url: str | None = Field(default=None, max_length=512)
    pairing_code: str | None = Field(default=None, max_length=256)
    device_name: str | None = Field(default=None, max_length=128)
    account_label: str | None = Field(default=None, max_length=64)
    read_only_capabilities: list[str] = Field(default_factory=list)
    confirm_read_only: bool = False
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("login")
    @classmethod
    def _login_valid(cls, v: str | None) -> str | None:
        if v is not None and not v.strip().isdigit():
            raise ValueError("MT5 login must be numeric")
        return v


class ExnessPairGatewayRequest(BaseModel):
    """Pair a gateway device (outbound, secure, short-lived pairing token)."""

    gateway_url: str = Field(min_length=8, max_length=512)
    device_name: str = Field(min_length=1, max_length=128)
    account_label: str | None = Field(default=None, max_length=64)
    pairing_code: str = Field(min_length=8, max_length=256)
    idempotency_key: str | None = Field(default=None, max_length=128)


class ExnessTestConnectionRequest(BaseModel):
    """Server-side test-connection payload for either connection mode."""

    mode: str = "gateway"  # gateway | server_side
    gateway_url: str | None = Field(default=None, max_length=512)
    pairing_code: str | None = Field(default=None, max_length=256)
    device_name: str | None = Field(default=None, max_length=128)
    login: str | None = Field(default=None, max_length=32)
    server: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=256)
    environment: str = "demo"
    idempotency_key: str | None = Field(default=None, max_length=128)

    @field_validator("mode")
    @classmethod
    def _mode_valid(cls, v: str) -> str:
        if v not in ("gateway", "server_side"):
            raise ValueError("mode must be 'gateway' or 'server_side'")
        return v


class ExnessDisconnectRequest(BaseModel):
    connection_id: str = Field(min_length=1, max_length=36)


class CapabilityReportOut(BaseModel):
    connection_status: str
    account_environment: str = "unknown"
    provider_server: str | None = None
    instrument_count: int = 0
    capabilities: dict[str, str] = {}
    historical_data_available: bool = False
    quote_availability: str = "none"
    account_metadata_available: bool = False
    data_delay_status: str = "realtime"
    latency_ms: float | None = None
    live_trading_status: str = "disabled"
    account_label: str | None = None
    detail: str | None = None


class ProviderConnectionOut(BaseModel):
    id: str
    provider: str
    display_name: str | None = None
    connection_mode: str | None = None
    environment: str | None = None
    status: str
    health_status: str | None = None
    last_connected_at: float | None = None
    last_successful_data_at: float | None = None
    last_error_code: str | None = None
    last_error_message_safe: str | None = None


class ProviderConnectionStatusCardOut(BaseModel):
    connection_status: str = "NOT_CONFIGURED"
    selected_provider: str = "No provider selected"
    display_name: str | None = None
    connection_mode: str | None = None
    environment: str | None = None
    account_type: str = "not_connected"  # demo | real | unknown | not_connected
    provider_server: str | None = None
    capabilities: list[str] = []
    available_capabilities: list[str] = []
    unavailable_capabilities: list[str] = []
    last_successful_data_utc: str | None = None
    latency_ms: float | None = None
    feed_health: str | None = None
    active_symbol_count: int = 0
    active_symbols: list[str] = []
    instrument_count: int = 0
    message: str = ""
    show_connect_button: bool = False
    live_trading_status: str = "disabled"