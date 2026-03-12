"""Double Distribution Reversal: 2 VP peaks, secondary weaker, rotation back to primary."""
from .detector import DetectorContext, SetupCandidate


def detect_double_distribution(ctx: DetectorContext) -> list[SetupCandidate]:
    """Detect Double Distribution — bimodal VP with weaker secondary peak."""
    candidates = []
    if not ctx.vp.levels or len(ctx.vp.levels) < 10:
        return []

    # Find two peaks in VP distribution
    poc = ctx.vp.poc
    poc_vol = max(l.volume for l in ctx.vp.levels)

    # Look for secondary peak: > 50% of POC volume, at least 5 ticks away
    secondary_peaks = []
    for level in ctx.vp.levels:
        if level.volume > poc_vol * 0.5 and abs(level.price - poc) > 5 * 0.25:
            secondary_peaks.append(level)

    if not secondary_peaks:
        return []

    # Secondary peak above POC → price should rotate down to primary
    for sec in secondary_peaks:
        if sec.price > poc and sec.volume < poc_vol:
            # Price near secondary peak → expect rotation down to POC
            if abs(ctx.last_price - sec.price) < (sec.price - poc) * 0.2:
                if ctx.macro_bias != "bull":
                    candidates.append(SetupCandidate(
                        setup_type="double_distribution",
                        setup_name="Double Distribution Short (rotate to POC)",
                        direction="short",
                        level_touched="secondary_vp_peak",
                        entry_price=ctx.last_price,
                        stop_price=sec.price * 1.002,
                        target_1=poc,
                        target_2=ctx.vp.val,
                        base_score=68.0,
                    ))

        elif sec.price < poc and sec.volume < poc_vol:
            if abs(ctx.last_price - sec.price) < (poc - sec.price) * 0.2:
                if ctx.macro_bias != "bear":
                    candidates.append(SetupCandidate(
                        setup_type="double_distribution",
                        setup_name="Double Distribution Long (rotate to POC)",
                        direction="long",
                        level_touched="secondary_vp_peak",
                        entry_price=ctx.last_price,
                        stop_price=sec.price * 0.998,
                        target_1=poc,
                        target_2=ctx.vp.vah,
                        base_score=68.0,
                    ))

    return candidates
