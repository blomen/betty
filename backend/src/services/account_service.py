"""Profile-create account provisioning + delete-time GC.

Owns the "use my shared sharp accounts" vs "create fresh sharp accounts" choice
when a profile is created, plus per-campaign soft-account signup, and cleans up
accounts when a profile is deleted (shared accounts survive; bet-less orphans are
hard-deleted, accounts with bet history are soft-deleted to preserve Stats).
"""

from sqlalchemy.orm import Session

from ..config import get_provider_currency
from ..constants import UNLIMITED_PROVIDERS
from ..db.models import Account, Profile, ProfileAccount
from ..repositories.account_repo import AccountRepo


class AccountService:
    def __init__(self, db: Session):
        self.db = db
        self.accounts = AccountRepo(db)

    def _edge_profile(self) -> Profile | None:
        """The profile whose sharp accounts are the shared pool: the active edge
        profile if present, else the lowest-id edge profile."""
        return (
            self.db.query(Profile).filter(Profile.kind == "edge").order_by(Profile.is_active.desc(), Profile.id).first()
        )

    def link_shared_sharp(self, profile: Profile) -> None:
        """Link the existing shared sharp accounts (the edge profile's) to this profile."""
        edge = self._edge_profile()
        if not edge or edge.id == profile.id:
            return
        for acct in self.accounts.accounts_for_profile(edge.id):
            if acct.provider_id in UNLIMITED_PROVIDERS and acct.kind == "sharp":
                self.accounts.link(profile.id, acct.id)

    def create_fresh_sharp(self, profile: Profile, label: str) -> None:
        """Create a new, independent set of sharp accounts linked only to this profile.

        The label MUST be unused by any existing sharp account — otherwise
        get_or_create would return the existing (possibly shared) account and the
        new profile would silently join that pool instead of getting isolated
        fresh accounts (e.g. label 'rasmus' would alias the shared pool, defeating
        ROI separation). Raises ValueError on collision so the route returns 400.
        """
        clash = self.db.query(Account).filter(Account.label == label, Account.kind == "sharp").first()
        if clash is not None:
            raise ValueError(
                f"Sharp account label '{label}' is already in use — pick a unique label for fresh accounts."
            )
        for prov in UNLIMITED_PROVIDERS:
            acct = self.accounts.get_or_create(prov, label, "sharp", get_provider_currency(prov))
            self.accounts.link(profile.id, acct.id)

    def create_soft(self, profile: Profile, providers: list[str]) -> None:
        """Create per-campaign soft accounts (labeled from the profile name)."""
        for prov in providers:
            acct = self.accounts.get_or_create(prov, profile.name, "soft", get_provider_currency(prov))
            self.accounts.link(profile.id, acct.id)

    def provision(
        self,
        profile: Profile,
        *,
        use_shared_sharp: bool,
        fresh_sharp_label: str | None,
        soft_providers: list[str] | None = None,
    ) -> None:
        """Wire a freshly-created profile's accounts per the create dialog choice."""
        if use_shared_sharp:
            self.link_shared_sharp(profile)
        elif fresh_sharp_label:
            self.create_fresh_sharp(profile, fresh_sharp_label)
        if soft_providers:
            self.create_soft(profile, soft_providers)

    def delete_profile_accounts(self, profile: Profile) -> None:
        """Unlink this profile's accounts; GC any that become orphaned.

        A shared sharp account linked to other profiles survives. An orphaned
        account (zero remaining links) is hard-deleted if it has no bets, else
        soft-deleted (is_active=False) so Stats history is preserved.
        """
        links = self.db.query(ProfileAccount).filter(ProfileAccount.profile_id == profile.id).all()
        account_ids = [link.account_id for link in links]
        for link in links:
            self.db.delete(link)
        self.db.flush()
        for aid in account_ids:
            if self.accounts.link_count(aid) > 0:
                continue  # still shared with another profile
            acct = self.accounts.get(aid)
            if acct is None:
                continue
            if self.accounts.has_bets(aid):
                acct.is_active = False
            else:
                self.db.delete(acct)
