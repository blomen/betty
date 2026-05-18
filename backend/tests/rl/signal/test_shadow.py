# backend/tests/rl/signal/test_shadow.py
import numpy as np

from src.rl.signal.protocol import ModelProtocol
from src.rl.signal.shadow import ShadowLogger
from src.rl.signal.types import MultiTaskOutputs


class _MockModel(ModelProtocol):
    def __init__(self, name: str, p_cont: float) -> None:
        super().__init__()
        self.name = name
        self._p_cont = p_cont

    def predict_raw(self, obs: np.ndarray) -> MultiTaskOutputs:
        return MultiTaskOutputs(
            direction_logits=[self._p_cont, 1 - self._p_cont - 0.1, 0.1],
            magnitude_R=1.0,
            win_probability=0.6,
            duration_bars=5.0,
            uncertainty=0.1,
        )


def test_shadow_logger_returns_production_signal_only():
    """The dispatch path gets the production model's signal."""
    prod = _MockModel("gbt_v5", p_cont=0.7)
    shadow = _MockModel("ft_v1", p_cont=0.3)

    written: list[dict] = []

    def fake_writer(records: list[dict]) -> None:
        written.extend(records)

    sl = ShadowLogger(
        production=prod,
        shadows=[shadow],
        db_writer=fake_writer,
    )
    obs = np.zeros(313, dtype=np.float32)
    sig = sl.predict(obs, zone_id=1, zone_center=25000.0, timestamp=100.0)

    assert sig.p_cont > 0.5  # production wins dispatch

    # Both models logged
    assert len(written) == 2
    assert {r["model_name"] for r in written} == {"gbt_v5", "ft_v1"}
    assert written[0]["request_id"] == written[1]["request_id"]
    prod_record = next(r for r in written if r["model_name"] == "gbt_v5")
    shadow_record = next(r for r in written if r["model_name"] == "ft_v1")
    assert prod_record["is_production"] is True
    assert shadow_record["is_production"] is False


def test_shadow_logger_continues_if_shadow_crashes():
    """A shadow model exception must NOT affect production dispatch."""
    prod = _MockModel("gbt_v5", p_cont=0.7)

    class _CrashyModel(ModelProtocol):
        name = "crashy"

        def predict_raw(self, obs):
            raise RuntimeError("simulated crash")

    written: list[dict] = []
    sl = ShadowLogger(
        production=prod,
        shadows=[_CrashyModel()],
        db_writer=lambda recs: written.extend(recs),
    )
    obs = np.zeros(313, dtype=np.float32)
    sig = sl.predict(obs, zone_id=2, zone_center=25001.0, timestamp=101.0)
    assert sig.p_cont > 0.5
    prod_records = [r for r in written if r["is_production"]]
    assert len(prod_records) == 1


def test_shadow_logger_with_no_shadows_passes_through():
    """If shadows=[] the logger should behave like a pure production wrapper."""
    prod = _MockModel("gbt_v5", p_cont=0.7)
    written: list[dict] = []
    sl = ShadowLogger(production=prod, shadows=[], db_writer=lambda recs: written.extend(recs))
    obs = np.zeros(313, dtype=np.float32)
    sig = sl.predict(obs, zone_id=3, zone_center=25002.0, timestamp=102.0)
    assert sig.p_cont > 0.5
    assert len(written) == 1  # production only
