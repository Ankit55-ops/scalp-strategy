from pydantic import BaseModel


class BrokerConnect(BaseModel):
    provider: str
    label: str
    api_key: str | None = None
    api_secret: str | None = None
    sandbox: bool = True


class BrokerOut(BaseModel):
    id: str
    provider: str
    label: str
    status: str
    is_sandbox: bool

    model_config = {"from_attributes": True}


class LiveDeploymentRequestCreate(BaseModel):
    strategy_id: str
    broker_connection_id: str
    risk_acknowledged: bool = False


class LiveDeploymentApprove(BaseModel):
    confirm: bool = True
