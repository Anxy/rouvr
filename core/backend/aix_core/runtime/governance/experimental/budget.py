"""001D-W07: Durable budget ledger — atomic reserve/consume with verification reserve."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from backend.aix_core.runtime.objective.contracts import BudgetV1, UsageV1


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


BudgetDisposition = Literal["RESERVED", "CONSUMED", "RELEASED", "DENIED"]


@dataclass(frozen=True)
class BudgetReservationV1:
    reservation_id: str
    cycle_id: str
    attempt_id: str
    purpose: str  # "step" | "verification" | "review"
    reserved: BudgetV1
    status: BudgetDisposition
    created_at: str


class BudgetLedgerError(ValueError):
    pass


class DurableBudgetLedger:
    """File-backed atomic budget ledger (process-local durability for local programme).

    Production durability remains owned by 001B PostgreSQL work; this ledger proves
    reserve/consume semantics and verification-budget protection without claiming PG.
    """

    def __init__(self, *, path: Path | None = None, ceiling: BudgetV1, verification_reserve: BudgetV1) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._ceiling = ceiling
        self._verification_reserve = verification_reserve
        self._consumed = BudgetV1(time_seconds=0, tokens=0, cost_usd=0.0, retries=0, steps=0, concurrency=1)
        self._reservations: dict[str, BudgetReservationV1] = {}
        if path and path.exists():
            self._load(path)

    def _remaining_for_steps(self) -> BudgetV1:
        # Hold verification reserve out of step dispatch capacity.
        return BudgetV1(
            time_seconds=max(0, self._ceiling.time_seconds - self._consumed.time_seconds - self._verification_reserve.time_seconds),
            tokens=max(0, self._ceiling.tokens - self._consumed.tokens - self._verification_reserve.tokens),
            cost_usd=max(0.0, self._ceiling.cost_usd - self._consumed.cost_usd - self._verification_reserve.cost_usd),
            retries=max(0, self._ceiling.retries - self._consumed.retries - self._verification_reserve.retries),
            steps=max(0, self._ceiling.steps - self._consumed.steps - self._verification_reserve.steps),
            concurrency=1,
        )

    def _fits(self, available: BudgetV1, need: BudgetV1) -> bool:
        return (
            need.time_seconds <= available.time_seconds
            and need.tokens <= available.tokens
            and need.cost_usd <= available.cost_usd
            and need.retries <= available.retries
            and need.steps <= available.steps
        )

    def reserve(
        self,
        *,
        reservation_id: str,
        cycle_id: str,
        attempt_id: str,
        purpose: str,
        need: BudgetV1,
    ) -> BudgetReservationV1:
        with self._lock:
            if reservation_id in self._reservations:
                raise BudgetLedgerError("duplicate reservation_id")
            if purpose == "verification":
                available = self._verification_reserve
                # verification draws from the protected reserve only once consumed from ceiling tracking separately
                if not self._fits(
                    BudgetV1(
                        time_seconds=self._verification_reserve.time_seconds,
                        tokens=self._verification_reserve.tokens,
                        cost_usd=self._verification_reserve.cost_usd,
                        retries=self._verification_reserve.retries,
                        steps=self._verification_reserve.steps,
                        concurrency=1,
                    ),
                    need,
                ):
                    raise BudgetLedgerError("verification_reserve_exhausted")
            else:
                available = self._remaining_for_steps()
                if not self._fits(available, need):
                    raise BudgetLedgerError("budget_exhausted")
            # Tentatively account reserved tokens against consumed to prevent double-spend.
            self._consumed = BudgetV1(
                time_seconds=self._consumed.time_seconds + need.time_seconds,
                tokens=self._consumed.tokens + need.tokens,
                cost_usd=round(self._consumed.cost_usd + need.cost_usd, 6),
                retries=self._consumed.retries + need.retries,
                steps=self._consumed.steps + need.steps,
                concurrency=1,
            )
            res = BudgetReservationV1(
                reservation_id=reservation_id,
                cycle_id=cycle_id,
                attempt_id=attempt_id,
                purpose=purpose,
                reserved=need,
                status="RESERVED",
                created_at=_utc(),
            )
            self._reservations[reservation_id] = res
            self._persist()
            return res

    def consume(self, reservation_id: str, *, usage: UsageV1 | None = None) -> BudgetReservationV1:
        with self._lock:
            res = self._reservations.get(reservation_id)
            if res is None:
                raise BudgetLedgerError("unknown_reservation")
            if res.status != "RESERVED":
                raise BudgetLedgerError(f"invalid_status:{res.status}")
            updated = BudgetReservationV1(
                reservation_id=res.reservation_id,
                cycle_id=res.cycle_id,
                attempt_id=res.attempt_id,
                purpose=res.purpose,
                reserved=res.reserved,
                status="CONSUMED",
                created_at=res.created_at,
            )
            self._reservations[reservation_id] = updated
            self._persist()
            return updated

    def release(self, reservation_id: str) -> BudgetReservationV1:
        with self._lock:
            res = self._reservations.get(reservation_id)
            if res is None:
                raise BudgetLedgerError("unknown_reservation")
            if res.status != "RESERVED":
                raise BudgetLedgerError(f"invalid_status:{res.status}")
            need = res.reserved
            self._consumed = BudgetV1(
                time_seconds=max(0, self._consumed.time_seconds - need.time_seconds),
                tokens=max(0, self._consumed.tokens - need.tokens),
                cost_usd=max(0.0, round(self._consumed.cost_usd - need.cost_usd, 6)),
                retries=max(0, self._consumed.retries - need.retries),
                steps=max(0, self._consumed.steps - need.steps),
                concurrency=1,
            )
            updated = BudgetReservationV1(
                reservation_id=res.reservation_id,
                cycle_id=res.cycle_id,
                attempt_id=res.attempt_id,
                purpose=res.purpose,
                reserved=res.reserved,
                status="RELEASED",
                created_at=res.created_at,
            )
            self._reservations[reservation_id] = updated
            self._persist()
            return updated

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "ceiling": self._ceiling.model_dump(),
                "verification_reserve": self._verification_reserve.model_dump(),
                "consumed": self._consumed.model_dump(),
                "reservations": {k: asdict(v) | {"reserved": v.reserved.model_dump()} for k, v in self._reservations.items()},
            }

    def _persist(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ceiling": self._ceiling.model_dump(),
            "verification_reserve": self._verification_reserve.model_dump(),
            "consumed": self._consumed.model_dump(),
            "reservations": {
                k: {
                    "reservation_id": v.reservation_id,
                    "cycle_id": v.cycle_id,
                    "attempt_id": v.attempt_id,
                    "purpose": v.purpose,
                    "reserved": v.reserved.model_dump(),
                    "status": v.status,
                    "created_at": v.created_at,
                }
                for k, v in self._reservations.items()
            },
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def _load(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self._ceiling = BudgetV1(**data["ceiling"])
        self._verification_reserve = BudgetV1(**data["verification_reserve"])
        self._consumed = BudgetV1(**data["consumed"])
        self._reservations = {}
        for k, v in data.get("reservations", {}).items():
            self._reservations[k] = BudgetReservationV1(
                reservation_id=v["reservation_id"],
                cycle_id=v["cycle_id"],
                attempt_id=v["attempt_id"],
                purpose=v["purpose"],
                reserved=BudgetV1(**v["reserved"]),
                status=v["status"],
                created_at=v["created_at"],
            )
