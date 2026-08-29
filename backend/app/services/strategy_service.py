"""Strategy service: create, version, validate rules via the safe DSL."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.dsl import validate_expression
from app.models import Strategy, StrategyRule, StrategyVersion
from app.schemas.strategy import StrategySpec


def validate_strategy_rules(spec: StrategySpec) -> list[dict]:
    """Validate each rule expression with the safe DSL. Returns per-rule results."""
    results = []
    for kind, rules in (("entry", spec.entry_rules), ("exit", spec.exit_rules)):
        for rule in rules:
            errors = validate_expression(rule.expression)
            results.append(
                {
                    "rule_id": rule.id,
                    "rule_type": kind,
                    "description": rule.description,
                    "is_valid": not errors,
                    "errors": errors,
                }
            )
    return results


def create_strategy(
    db: Session,
    workspace_id: str,
    spec: StrategySpec,
    notes: str = "",
) -> Strategy:
    strategy = Strategy(
        workspace_id=workspace_id,
        name=spec.name,
        strategy_family=spec.strategy_family.value,
        current_version=spec.version,
        status="active",
        spec=spec.model_dump(mode="json"),
    )
    db.add(strategy)
    db.flush()

    db.add(
        StrategyVersion(
            strategy_id=strategy.id,
            version=spec.version,
            spec=spec.model_dump(mode="json"),
            notes=notes,
        )
    )

    for kind, rules in (("entry", spec.entry_rules), ("exit", spec.exit_rules)):
        for rule in rules:
            errors = validate_expression(rule.expression)
            db.add(
                StrategyRule(
                    strategy_id=strategy.id,
                    rule_id=rule.id,
                    rule_type=kind,
                    description=rule.description,
                    expression=rule.expression,
                    is_valid=not errors,
                    validation_errors=errors or None,
                )
            )
    db.commit()
    db.refresh(strategy)
    return strategy


def add_version(db: Session, strategy: Strategy, spec: StrategySpec, notes: str = "") -> StrategyVersion:
    # Bump minor version.
    parts = (strategy.current_version or "1.0.0").split(".")
    try:
        new_version = f"{parts[0]}.{int(parts[1]) + 1}.0"
    except (IndexError, ValueError):
        new_version = "1.1.0"
    version = StrategyVersion(
        strategy_id=strategy.id,
        version=new_version,
        spec=spec.model_dump(mode="json"),
        notes=notes,
    )
    strategy.current_version = new_version
    strategy.spec = spec.model_dump(mode="json")
    db.add(version)
    db.commit()
    db.refresh(version)
    return version
