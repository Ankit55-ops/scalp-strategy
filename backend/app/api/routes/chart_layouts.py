from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_workspace
from app.db.session import get_db
from app.models import SavedChartLayout, User, Workspace
from app.schemas.chart import ChartLayoutCreate, ChartLayoutOut

router = APIRouter(prefix="/chart-layouts", tags=["chart-layouts"])


@router.get("", response_model=list[ChartLayoutOut])
def list_layouts(
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> list[ChartLayoutOut]:
    rows = (
        db.query(SavedChartLayout)
        .filter(SavedChartLayout.workspace_id == ws.id)
        .order_by(SavedChartLayout.created_at.desc())
        .all()
    )
    return [
        ChartLayoutOut(
            id=r.id,
            name=r.name,
            symbol=r.symbol,
            timeframe=r.timeframe,
            layout=r.layout,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.post("", response_model=ChartLayoutOut, status_code=201)
def save_layout(
    payload: ChartLayoutCreate,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> ChartLayoutOut:
    row = SavedChartLayout(
        workspace_id=ws.id,
        name=payload.name,
        symbol=payload.symbol.upper(),
        timeframe=payload.timeframe,
        layout=payload.layout,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return ChartLayoutOut(
        id=row.id,
        name=row.name,
        symbol=row.symbol,
        timeframe=row.timeframe,
        layout=row.layout,
        created_at=row.created_at.isoformat(),
    )


@router.delete("/{layout_id}", response_model=dict)
def delete_layout(
    layout_id: str,
    user: User = Depends(get_current_user),
    ws: Workspace = Depends(get_current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(SavedChartLayout, layout_id)
    if not row or row.workspace_id != ws.id:
        raise HTTPException(status_code=404, detail="layout not found")
    db.delete(row)
    db.commit()
    return {"id": layout_id, "deleted": True}