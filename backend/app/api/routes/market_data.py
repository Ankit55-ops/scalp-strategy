"""Market data endpoints: list available symbols and import CSV candle files."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.providers.factory import get_market_data_provider

router = APIRouter(prefix="/market-data", tags=["market-data"])


@router.get("/symbols", response_model=list[dict])
def list_symbols(
    user: User = Depends(get_current_user),
) -> list[dict]:
    provider = get_market_data_provider("csv")
    symbols = [
        {"symbol": s, "provider": "csv"} for s in provider.list_symbols()
    ]
    # always include the known registered forex symbols too
    return symbols


@router.post("/import", response_model=dict)
async def import_csv(
    symbol: str = "",
    timeframe: str = "M5",
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not symbol.strip():
        raise HTTPException(status_code=422, detail="symbol is required")
    import uuid

    tmp = f"/tmp/fxscalper_upload_{uuid.uuid4().hex}.csv"
    content = await file.read()
    with open(tmp, "wb") as f:
        f.write(content)
    try:
        provider = get_market_data_provider("csv")
        count = provider.import_file(symbol.upper(), timeframe, tmp)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"failed to import: {exc}") from exc
    finally:
        import os

        if os.path.exists(tmp):
            os.remove(tmp)
    return {"imported": count, "symbol": symbol.upper(), "timeframe": timeframe}