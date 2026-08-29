"""Kill-switch registry: global, per-strategy, per-pair.

State is persisted in the database per workspace (single source of truth,
survives restarts and is visible to the Risk Center). When no DB session is
provided (e.g. unit tests of the risk engine) an in-memory fallback is used.

An engaged switch blocks new entries at the risk engine and is only removed by
an explicit manual (or automated-monitor) disarm.
"""

from __future__ import annotations

from app.models import KillSwitch


class KillSwitchRegistry:
    def __init__(self, db=None, workspace_id: str | None = None) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self._mem: dict[tuple[str, str], bool] = {}

    # -- helpers ------------------------------------------------------------
    def _query_row(self, scope: str, resource_id: str):
        return (
            self.db.query(KillSwitch)
            .filter(
                KillSwitch.workspace_id == self.workspace_id,
                KillSwitch.scope == scope,
                KillSwitch.resource_id == resource_id,
            )
            .first()
        )

    def _upsert(self, scope: str, resource_id: str, enabled: bool, reason: str | None) -> None:
        if self.db is not None and self.workspace_id:
            row = self._query_row(scope, resource_id)
            if enabled:
                if row is None:
                    self.db.add(
                        KillSwitch(
                            workspace_id=self.workspace_id,
                            scope=scope,
                            resource_id=resource_id,
                            enabled=True,
                            reason=reason,
                        )
                    )
                else:
                    row.enabled = True
                    if reason:
                        row.reason = reason
                self.db.commit()
            elif row is not None:
                self.db.delete(row)
                self.db.commit()
            return
        self._mem[(scope, resource_id)] = enabled

    # -- scopes ------------------------------------------------------------
    def set_global(self, enabled: bool, reason: str | None = None) -> None:
        self._upsert("global", "global", enabled, reason)

    def set_strategy(self, strategy_id: str, enabled: bool, reason: str | None = None) -> None:
        self._upsert("strategy", str(strategy_id), enabled, reason)

    def set_pair(self, symbol: str, enabled: bool, reason: str | None = None) -> None:
        self._upsert("pair", str(symbol).upper().replace("/", ""), enabled, reason)

    # -- checks ------------------------------------------------------------
    def is_halted(self, symbol: str | None, strategy_id: str | None = None) -> bool:
        if self.db is not None and self.workspace_id:
            rows = (
                self.db.query(KillSwitch)
                .filter(
                    KillSwitch.workspace_id == self.workspace_id,
                    KillSwitch.enabled.is_(True),
                )
                .all()
            )
            symbol = str(symbol).upper().replace("/", "") if symbol else symbol
            for r in rows:
                if r.scope == "global":
                    return True
                if r.scope == "strategy" and strategy_id and r.resource_id == str(strategy_id):
                    return True
                if r.scope == "pair" and symbol and r.resource_id == symbol:
                    return True
            return False

        halted = sum(v for k, v in self._mem.items() if k[0] == "global") > 0
        if halted:
            return True
        if strategy_id and self._mem.get(("strategy", str(strategy_id))):
            return True
        if symbol and self._mem.get(("pair", str(symbol).upper().replace("/", ""))):
            return True
        return False

    def is_global_halted(self) -> bool:
        return self.is_halted(symbol=None)

    def status(self) -> dict:
        strategy: dict[str, bool] = {}
        pair: dict[str, bool] = {}
        global_halted = False

        if self.db is not None and self.workspace_id:
            rows = (
                self.db.query(KillSwitch)
                .filter(
                    KillSwitch.workspace_id == self.workspace_id,
                    KillSwitch.enabled.is_(True),
                )
                .all()
            )
            for r in rows:
                if r.scope == "global":
                    global_halted = True
                elif r.scope == "strategy":
                    strategy[r.resource_id] = True
                elif r.scope == "pair":
                    pair[r.resource_id] = True
        else:
            for (scope, rid), value in self._mem.items():
                if not value:
                    continue
                if scope == "global":
                    global_halted = True
                elif scope == "strategy":
                    strategy[rid] = True
                elif scope == "pair":
                    pair[rid] = True

        return {"global": global_halted, "strategy": strategy, "pair": pair}

    def list_engagements(self) -> list[dict]:
        """Active (engaged) switches as rows: [{scope, resource_id, reason}]."""
        if self.db is not None and self.workspace_id:
            rows = (
                self.db.query(KillSwitch)
                .filter(
                    KillSwitch.workspace_id == self.workspace_id,
                    KillSwitch.enabled.is_(True),
                )
                .all()
            )
            return [
                {"scope": r.scope, "resource_id": r.resource_id, "reason": r.reason}
                for r in rows
            ]
        out = []
        for (scope, rid), value in self._mem.items():
            if value:
                out.append({"scope": scope, "resource_id": rid, "reason": None})
        return out

    def reset(self) -> None:
        if self.db is not None and self.workspace_id:
            for row in self.db.query(KillSwitch).filter(KillSwitch.workspace_id == self.workspace_id).all():
                self.db.delete(row)
            self.db.commit()
            return
        self._mem.clear()