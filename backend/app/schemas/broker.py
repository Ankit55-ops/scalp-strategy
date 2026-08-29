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


class BrokerUpdate(BaseModel):
    label: str | None = None
    status: str | None = None


class BrokerConnectTest(BaseModel):
    api_key: str | None = None


class LiveDeploymentRequestCreate(BaseModel):
    strategy_id: str
    broker_connection_id: str
    risk_acknowledged: bool = False


class LiveDeploymentApprove(BaseModel):
    confirm: bool = True


class LiveDeploymentReject(BaseModel):
    reason: str = "rejected by reviewer"
