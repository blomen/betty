"""ShadowLogger — runs the production model + 0..N shadow models on every
zone touch, logs all predictions to the shadow_predictions table, and
returns the production model's Signal for dispatch.

Critical safety property: a shadow model's exception must NEVER affect
the production prediction or dispatch. We catch everything from shadows
and log it, then continue.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

import numpy as np

from .protocol import ModelProtocol
from .types import Signal

log = logging.getLogger(__name__)


class ShadowLogger:
    def __init__(
        self,
        production: ModelProtocol,
        shadows: list[ModelProtocol],
        db_writer: Callable[[list[dict]], None],
    ) -> None:
        """
        production: the ModelProtocol whose Signal is dispatched
        shadows: ModelProtocols whose Signals are logged only
        db_writer: function that takes a list of dicts and persists them
                   (typically wraps SQLAlchemy bulk insert)
        """
        self._production = production
        self._shadows = shadows
        self._db_writer = db_writer

    def predict(
        self,
        obs: np.ndarray,
        *,
        zone_id: int,
        zone_center: float,
        timestamp: float,
    ) -> Signal:
        request_id = uuid.uuid4().hex
        records: list[dict] = []

        # 1. Production — must succeed
        prod_signal = self._production.predict(obs, zone_id=zone_id, timestamp=timestamp)
        records.append(
            self._signal_to_record(
                signal=prod_signal,
                request_id=request_id,
                model_name=getattr(self._production, "name", "production"),
                is_production=True,
                zone_id=zone_id,
                zone_center=zone_center,
            )
        )

        # 2. Each shadow — best-effort
        for shadow in self._shadows:
            try:
                shadow_signal = shadow.predict(obs, zone_id=zone_id, timestamp=timestamp)
                records.append(
                    self._signal_to_record(
                        signal=shadow_signal,
                        request_id=request_id,
                        model_name=getattr(shadow, "name", shadow.__class__.__name__),
                        is_production=False,
                        zone_id=zone_id,
                        zone_center=zone_center,
                    )
                )
            except Exception:
                log.exception("shadow model %s raised; production unaffected", shadow)

        # 3. Best-effort log — write failure shouldn't affect dispatch
        try:
            self._db_writer(records)
        except Exception:
            log.exception("shadow log write failed")

        return prod_signal

    @staticmethod
    def _signal_to_record(
        signal: Signal,
        request_id: str,
        model_name: str,
        is_production: bool,
        zone_id: int,
        zone_center: float,
    ) -> dict:
        return {
            "request_id": request_id,
            "model_name": model_name,
            "is_production": is_production,
            "p_cont": signal.p_cont,
            "p_rev": signal.p_rev,
            "p_skip": signal.p_skip,
            "expected_R": signal.expected_R,
            "win_probability": signal.win_probability,
            "duration_bars": signal.duration_bars,
            "uncertainty": signal.uncertainty,
            "confidence": signal.confidence,
            "action": signal.action,
            "zone_id": zone_id,
            "zone_center": zone_center,
        }
