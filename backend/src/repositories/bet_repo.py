"""Bet repository - bet data access."""

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from ..db.models import Bet


class BetRepo:
    """Data access for bets."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, bet_id: int) -> Bet | None:
        """Get bet by ID."""
        return self.db.query(Bet).filter(Bet.id == bet_id).first()

    def get_settled(self, profile_id: int) -> list[Bet]:
        """Get settled bets for a profile."""
        return (
            self.db.query(Bet)
            .filter(
                Bet.result != "pending",
                Bet.profile_id == profile_id,
            )
            .all()
        )

    def get_settled_aggregates(self, profile_id: int) -> list:
        """Aggregate settled bets by (provider_id, currency, result, is_bonus).

        Used by `BankrollService.get_stats` to avoid materialising every
        settled bet just to recompute totals/counts. The (provider_id,
        currency) tuple is the natural grain for currency-conversion since
        `get_exchange_rate(provider_id)` returns the same rate for all bets
        at one provider.

        Returns Row objects with attributes:
        - provider_id, currency, result, is_bonus (group keys)
        - cnt: COUNT(id)
        - sum_stake, sum_payout: SUM of money columns
        - clv_count: COUNT(clv_pct)  (non-null)
        - clv_sum: SUM(clv_pct)
        - clv_positive_count: COUNT(WHERE clv_pct > 0)

        At ~16 providers × ~3 currencies × 3 results × 2 bonus_flags this
        returns at most ~288 rows regardless of bet history depth, vs the
        prior approach which loaded every settled bet (thousands+).
        """
        from ..db.models import Profile

        return (
            self.db.query(
                Bet.provider_id.label("provider_id"),
                Bet.currency.label("currency"),
                Bet.result.label("result"),
                Bet.is_bonus.label("is_bonus"),
                Profile.kind.label("kind"),
                func.count(Bet.id).label("cnt"),
                func.coalesce(func.sum(Bet.stake), 0.0).label("sum_stake"),
                func.coalesce(func.sum(Bet.payout), 0.0).label("sum_payout"),
                func.count(Bet.clv_pct).label("clv_count"),
                func.coalesce(func.sum(Bet.clv_pct), 0.0).label("clv_sum"),
                func.coalesce(func.sum(case((Bet.clv_pct > 0, 1), else_=0)), 0).label("clv_positive_count"),
            )
            .join(Profile, Profile.id == Bet.profile_id)
            .filter(
                Bet.result != "pending",
                Bet.profile_id == profile_id,
            )
            .group_by(Bet.provider_id, Bet.currency, Bet.result, Bet.is_bonus, Profile.kind)
            .all()
        )

    def get_bonus_profit_aggregates(self) -> list:
        """Settled bonus-extraction profit across ALL profiles, grouped for SEK conversion.

        Rule B: a bonus-extraction campaign's profit (the soft free-bet leg AND
        the real-money sharp hedge leg) is tracked separately from true ROI. A bet
        counts here when EITHER:
          - it's under a kind='bonus' profile (both legs), OR
          - it's flagged is_bonus on any profile (a stray free-bet placed on an
            edge profile — is_bonus predates the profile-kind model).
        These two sets exactly complement the ROI aggregate (which counts only
        `not is_bonus AND kind='edge'` rows), so every settled bet lands in
        exactly one bucket — never double-counted, never dropped. Grouped by
        (provider_id, currency, result, is_bonus) so the caller can convert to
        SEK per provider and apply the same Bet.profit semantics.
        """
        from ..db.models import Profile

        return (
            self.db.query(
                Bet.provider_id.label("provider_id"),
                Bet.currency.label("currency"),
                Bet.result.label("result"),
                Bet.is_bonus.label("is_bonus"),
                func.coalesce(func.sum(Bet.stake), 0.0).label("sum_stake"),
                func.coalesce(func.sum(Bet.payout), 0.0).label("sum_payout"),
            )
            .join(Profile, Profile.id == Bet.profile_id)
            .filter(
                Bet.result != "pending",
                or_(Profile.kind == "bonus", Bet.is_bonus.is_(True)),
            )
            .group_by(Bet.provider_id, Bet.currency, Bet.result, Bet.is_bonus)
            .all()
        )

    def get_pending_for_provider(self, provider_id: str, profile_id: int) -> list[Bet]:
        """Get pending bets for a provider and profile."""
        return (
            self.db.query(Bet)
            .filter(
                Bet.provider_id == provider_id,
                Bet.profile_id == profile_id,
                Bet.result == "pending",
            )
            .all()
        )

    def recorded_provider_bet_ids(self, profile_id: int, provider_id: str) -> set[str]:
        """All non-null provider_bet_id values for a provider, ANY result.

        The position-based recorders (polymarket/kalshi) dedup against this so
        a settled-and-lingering position is never re-inserted. Deduping on
        pending-only rows re-inserts a position every sync once it settles.
        """
        rows = (
            self.db.query(Bet.provider_bet_id)
            .filter(
                Bet.profile_id == profile_id,
                Bet.provider_id == provider_id,
                Bet.provider_bet_id.isnot(None),
            )
            .all()
        )
        return {r[0] for r in rows if r[0]}

    def list_for_profile(
        self,
        profile_id: int,
        status: str | None = None,
        exclude_bonus: bool = False,
        limit: int = 50,
    ) -> list[Bet]:
        """List bets for a profile with optional status filter."""
        query = self.db.query(Bet).filter(Bet.profile_id == profile_id)
        if status:
            query = query.filter(Bet.result == status)
        if exclude_bonus:
            query = query.filter(~Bet.is_bonus)
        return query.order_by(Bet.placed_at.desc()).limit(limit).all()

    def get_settled_for_curve(self, profile_id: int, cutoff=None) -> list:
        """Ordered settled bets for the equity curve — minimal columns, no enrichment.

        Returns Row objects with: placed_at, result, payout, stake, currency,
        provider_id, is_bonus. Ordered by placed_at ASC.
        """
        q = self.db.query(
            Bet.placed_at,
            Bet.result,
            Bet.payout,
            Bet.stake,
            Bet.currency,
            Bet.provider_id,
            Bet.is_bonus,
        ).filter(
            Bet.profile_id == profile_id,
            Bet.result.in_(("won", "lost", "void")),
        )
        if cutoff is not None:
            q = q.filter(Bet.placed_at >= cutoff)
        return q.order_by(Bet.placed_at.asc()).all()

    def create(self, **kwargs) -> Bet:
        """Create a new bet record.

        If account_id isn't supplied, resolve it from (profile_id, provider_id)
        via the Account layer so every bet is attributed to a real account
        (shared sharp pool or per-campaign soft account). Resolve-only: if the
        profile has no linked account for the provider yet, account_id stays
        NULL — ROI bucketing keys on profile.kind, not account_id.
        """
        if not kwargs.get("account_id"):
            profile_id = kwargs.get("profile_id")
            provider_id = kwargs.get("provider_id")
            if profile_id is not None and provider_id:
                from .account_repo import AccountRepo

                # account_id is attribution, not a correctness gate (ROI buckets
                # on profile.kind). Never let a resolution hiccup drop a bet —
                # CLAUDE.md: recorders must not silently lose bets. On error,
                # leave account_id NULL and record the bet anyway.
                try:
                    acct = AccountRepo(self.db).resolve(profile_id, provider_id)
                    if acct is not None:
                        kwargs["account_id"] = acct.id
                except Exception:
                    pass
        bet = Bet(**kwargs)
        self.db.add(bet)
        return bet
