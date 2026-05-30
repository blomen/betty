"""Account repository — shared/labeled real accounts + per-profile visibility.

An Account is one real account at a provider, identified by (provider_id, label).
Sharp accounts are shared across profiles via the profile_accounts link table;
soft accounts are per-campaign. This repo is the single access point for account
balance reads/writes and link queries (CLAUDE.md: no raw session.query in
routes/services).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..db.models import Account, Bet, ProfileAccount


class AccountRepo:
    """Data access for accounts and profile→account links."""

    def __init__(self, db: Session):
        self.db = db

    # ---- Lookup ----

    def get(self, account_id: int) -> Account | None:
        return self.db.query(Account).filter(Account.id == account_id).first()

    def get_or_create(self, provider_id: str, label: str, kind: str, currency: str) -> Account:
        """Return the (provider_id, label) account, creating it if absent.

        If a soft-deleted account with this (provider_id, label) exists — e.g. a
        campaign profile was deleted (its account soft-deleted because it had
        bets) and a new profile reuses the same name — reactivate it rather than
        leave a dead row that resolve() (is_active-filtered) would skip, which
        would strand the new profile with no usable account. Reactivating
        preserves the account's bet history.
        """
        acct = self.db.query(Account).filter(Account.provider_id == provider_id, Account.label == label).first()
        if acct is not None:
            if not acct.is_active:
                acct.is_active = True
                acct.updated_at = datetime.now(UTC)
            return acct
        acct = Account(provider_id=provider_id, label=label, kind=kind, currency=currency, is_active=True)
        self.db.add(acct)
        self.db.flush()
        return acct

    def resolve(self, profile_id: int, provider_id: str) -> Account | None:
        """The single active account this profile uses for a provider, or None.

        Profile-create guarantees at most one active account per provider per
        profile, so the lowest-id active linked account is unambiguous.
        """
        return (
            self.db.query(Account)
            .join(ProfileAccount, ProfileAccount.account_id == Account.id)
            .filter(
                ProfileAccount.profile_id == profile_id,
                Account.provider_id == provider_id,
                Account.is_active,
            )
            .order_by(Account.id)
            .first()
        )

    def accounts_for_profile(self, profile_id: int) -> list[Account]:
        """All active accounts linked to a profile."""
        return (
            self.db.query(Account)
            .join(ProfileAccount, ProfileAccount.account_id == Account.id)
            .filter(ProfileAccount.profile_id == profile_id, Account.is_active)
            .all()
        )

    def balances_map(self, profile_id: int) -> dict[str, float]:
        """`{provider_id: balance}` for every active account linked to a profile.

        Single source of truth for "what balances does this profile see" — every
        live balance reader (bankroll display, allocators, opportunity sizing,
        simulator) goes through this so shared sharp pools are reflected
        everywhere, not just the per-profile legacy table.
        """
        return {a.provider_id: (a.balance or 0.0) for a in self.accounts_for_profile(profile_id)}

    def distinct_accounts(self) -> list[Account]:
        """All active accounts, each once — for cross-profile grand totals."""
        return self.db.query(Account).filter(Account.is_active).all()

    # ---- Links ----

    def link(self, profile_id: int, account_id: int) -> None:
        exists = (
            self.db.query(ProfileAccount)
            .filter(ProfileAccount.profile_id == profile_id, ProfileAccount.account_id == account_id)
            .first()
        )
        if not exists:
            self.db.add(ProfileAccount(profile_id=profile_id, account_id=account_id))

    def unlink(self, profile_id: int, account_id: int) -> None:
        self.db.query(ProfileAccount).filter(
            ProfileAccount.profile_id == profile_id, ProfileAccount.account_id == account_id
        ).delete()

    def link_count(self, account_id: int) -> int:
        return self.db.query(ProfileAccount).filter(ProfileAccount.account_id == account_id).count()

    # ---- Mutation ----

    def set_balance(self, account_id: int, balance: float) -> None:
        acct = self.get(account_id)
        if acct:
            acct.balance = balance
            acct.updated_at = datetime.now(UTC)

    def has_bets(self, account_id: int) -> bool:
        return self.db.query(Bet.id).filter(Bet.account_id == account_id).first() is not None
