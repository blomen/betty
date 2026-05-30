# Multi-Profile Sharp Accounts + Bonus-Profit Accounting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each profile reuse the shared sharp accounts (one real balance, labeled e.g. `POLY (rasmus)`) or create fresh ones, and keep bonus-extraction profit (both legs) out of "true ROI" by bucketing it separately.

**Architecture:** Introduce a first-class `accounts` table (one row per real account = `(provider_id, label)`) plus a `profile_accounts` link table for explicit per-profile visibility. `ProfileProviderBalance` is replaced as the balance store; `ProfileRepo`'s existing balance methods keep their `(profile_id, provider_id)` signatures but resolve through the link table to the shared `accounts.balance`, so sharp accounts become genuinely shared with no change to the ~15 existing call sites. A new `profiles.kind ∈ {edge, bonus}` drives Rule-B ROI bucketing: every bet under a `bonus` profile (soft bonus leg + sharp hedge leg) is excluded from true ROI and summed into a separate `bonus_profit`.

**Tech Stack:** Python 3.12 / SQLAlchemy (ORM in `backend/src/db/models.py`, raw-cursor in-`init_db` migrations) / FastAPI / pytest · React 19 / TypeScript / Vite.

**Spec:** [2026-05-30-multi-profile-sharp-accounts-bonus-roi.md](2026-05-30-multi-profile-sharp-accounts-bonus-roi.md)

---

## Critical context for the implementer

- **DO NOT deploy.** Production runs on Hetzner; deploying is the user's call. This
  plan ends at "green locally". The migration touches the live schema — it must be
  idempotent and verified on a DB copy before any deploy.
- **Currency rule (CLAUDE.md):** never sum balances/stakes across providers without
  converting to SEK first via `get_exchange_rate(provider_id)`. The existing code
  already does this; preserve it.
- **Models file is ORM-only** — no logic. Migrations live in the `init_db()` block
  in `backend/src/db/models.py` as `cursor.execute` calls guarded by a probe
  `SELECT` in `try/except` (see the existing `chrome_port` / `wallet_address`
  migrations ~lines 1621-1660 for the exact pattern to copy).
- **Repo layer is the only DB access point** — no raw `session.query` in
  routes/services (CLAUDE.md). New account access goes in `AccountRepo`.
- **Sharp providers** = `UNLIMITED_PROVIDERS` in `backend/src/constants.py`
  (`pinnacle`, `polymarket`, `kalshi`, `cloudbet`). Use that constant; do not
  hardcode the list.
- **Mirror invariants (CLAUDE.md):** when stamping `account_id` on recorded bets,
  do NOT change dedup keys, the `_record_manual_bet` no-fallback rule, or any
  navigation behaviour. Add the field only.
- Run backend tests with `cd backend && pytest`. Lint: `ruff check backend/src`
  (auto-runs on save via hook) and `cd frontend && npm run lint`.

## Resolved decisions (from spec open questions)

1. Accounts with bets are **soft-deleted** (`is_active=False`), never hard-deleted.
   Bet-less orphan accounts are hard-deleted on profile delete.
2. `bets.account_id` FK is `ON DELETE SET NULL`.
3. Migration label default for the existing shared sharp pool: `rasmus`.
4. `bonus_profit` in Stats is **all-profiles** (total harvested), not active-only.
5. `AccountRepo.resolve(profile_id, provider_id)` returns the single active linked
   account for that provider; the profile-create service guarantees at most one
   active account per provider per profile, so resolution is unambiguous.

## File map

| File | Change |
|---|---|
| `backend/src/db/models.py` | + `Account`, `ProfileAccount` models; `Profile.kind`; `Bet.account_id`; migration block; drop `ProfileProviderBalance` after backfill |
| `backend/src/repositories/account_repo.py` | **new** — account CRUD + resolver + link queries |
| `backend/src/repositories/__init__.py` | export `AccountRepo` |
| `backend/src/repositories/profile_repo.py` | re-point balance methods through `AccountRepo`; replace `copy_balances` |
| `backend/src/services/account_service.py` | **new** — profile-create wiring (link shared / create fresh / soft signup), delete GC |
| `backend/src/services/bankroll_service.py` | `get_bankroll` returns labels; `get_stats` Rule-B + `bonus_profit` |
| `backend/src/repositories/bet_repo.py` | `get_settled_aggregates` carries profile `kind`; `create` accepts `account_id`; + all-profiles bonus aggregate |
| `backend/src/api/schemas.py` | `ProfileCreate` + `kind`, `use_shared_sharp`, `fresh_sharp_label`; `BetCreate` + `account_id` |
| `backend/src/api/routes/profiles.py` | create route calls `AccountService`; response includes `kind` |
| `backend/src/api/routes/bankroll.py` | `set/{provider_id}` unchanged signature (repo redirect handles it) |
| `local/mirror/play_loop.py`, `local/mirror/pending_loop.py` | stamp `account_id` on insert |
| `frontend/src/components/ProfileSelector.tsx` | create form: purpose + sharp choice + label |
| `frontend/src/hooks/useProfiles.ts` | `createProfile` accepts full `ProfileCreate` |
| `frontend/src/services/api/profiles.ts` | (no change — already posts `ProfileCreate`) |
| `frontend/src/types/index.ts` | `Profile.kind`; `ProfileCreate` new fields |
| `frontend/src/pages/BankrollPage.tsx` | render `PROVIDER (label)`; bonus_profit already in `BankrollStats` |
| `frontend/src/pages/StatsPage.tsx` | render `bonus_profit` distinct from ROI |
| `backend/tests/...` | new tests per task |

---

## Task 1: Add `Account` + `ProfileAccount` models and new columns

**Files:**
- Modify: `backend/src/db/models.py` (add models after `ProfileProviderBalance` ~line 750; add `kind` to `Profile` ~line 647; add `account_id` to `Bet` ~line 317)
- Test: `backend/tests/db/test_account_models.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/db/test_account_models.py
"""Account / ProfileAccount model + new-column smoke tests (sqlite in-memory)."""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.src.db.models import Base, Account, ProfileAccount, Profile, Bet, Provider


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_account_and_link_roundtrip():
    s = _session()
    s.add(Provider(id="polymarket", name="Polymarket"))
    p = Profile(name="edge", kind="edge", is_active=True)
    s.add(p)
    s.flush()
    acct = Account(provider_id="polymarket", label="rasmus", kind="sharp",
                   balance=76.29, currency="USDC", is_active=True)
    s.add(acct)
    s.flush()
    s.add(ProfileAccount(profile_id=p.id, account_id=acct.id))
    s.flush()
    assert acct.id is not None
    # relationship wiring
    assert p.accounts[0].account.label == "rasmus"
    assert acct.profile_links[0].profile_id == p.id


def test_profile_kind_defaults_edge():
    s = _session()
    p = Profile(name="x")
    s.add(p); s.flush()
    assert p.kind == "edge"


def test_bet_account_id_nullable():
    s = _session()
    s.add(Provider(id="pinnacle", name="Pinnacle"))
    p = Profile(name="x"); s.add(p); s.flush()
    b = Bet(profile_id=p.id, provider_id="pinnacle", odds=2.0, stake=10.0,
            currency="SEK", result="pending")
    s.add(b); s.flush()
    assert b.account_id is None
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && pytest tests/db/test_account_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Account'`.

- [ ] **Step 3: Add the models + columns**

In `backend/src/db/models.py`, add `kind` to `Profile` (after `is_active`, ~line 647):

```python
    kind = Column(String, default="edge", nullable=False)  # "edge" | "bonus" — drives ROI bucketing
```

Add `account_id` to `Bet` (after `profile_id`, ~line 317):

```python
    # Which real account this bet was placed from (shared sharp pool or a
    # per-campaign soft account). Source of truth for account attribution;
    # provider_id is retained for all existing readers. SET NULL so GC'd
    # soft accounts don't orphan-delete bet history.
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)
```

Add the two new models after `ProfileProviderBalance` (~line 750), matching the file's style:

```python
class Account(Base):
    """One real account the user owns at a provider.

    Sharp accounts (pinnacle/polymarket/kalshi/cloudbet) are SHARED: a single
    row referenced by many profiles via `profile_accounts`. Spending a hedge
    leg in any profile updates this one real `balance`. Soft accounts are
    per-campaign (single-linked). Replaces ProfileProviderBalance as the
    balance store.
    """

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(String, ForeignKey("providers.id"), nullable=False)
    label = Column(String, nullable=False)  # "rasmus", "alt2", "campaign-7"
    kind = Column(String, nullable=False)  # "sharp" | "soft"
    balance = Column(Float, default=0.0)
    currency = Column(String, default="SEK")  # native currency for conversion
    account_opened_at = Column(DateTime, nullable=True)  # dormant-account handling (carried from PPB)
    is_active = Column(Boolean, default=True, nullable=False)  # soft-delete flag
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("provider_id", "label", name="uq_account_provider_label"),
        Index("ix_account_provider", "provider_id"),
    )

    provider = relationship("Provider")
    profile_links = relationship("ProfileAccount", back_populates="account", cascade="all, delete-orphan")


class ProfileAccount(Base):
    """Explicit visibility: a profile sees exactly the accounts linked here.

    A fresh sharp account is linked only to the profile that created it, so it
    does NOT leak into other profiles. Shared sharp accounts get one link row
    per profile that uses them.
    """

    __tablename__ = "profile_accounts"

    profile_id = Column(Integer, ForeignKey("profiles.id"), primary_key=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), primary_key=True)
    created_at = Column(DateTime, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("profile_id", "account_id", name="uq_profile_account"),
    )

    account = relationship("Account", back_populates="profile_links")
    profile = relationship("Profile", back_populates="accounts")
```

Add the back-populate on `Profile` (in its relationships block, ~line 657):

```python
    accounts = relationship("ProfileAccount", back_populates="profile", cascade="all, delete-orphan")
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd backend && pytest tests/db/test_account_models.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

```bash
ruff check backend/src/db/models.py
git add backend/src/db/models.py backend/tests/db/test_account_models.py
git commit -m "feat(db): add Account + ProfileAccount models, Profile.kind, Bet.account_id"
```

---

## Task 2: Migration — backfill accounts from `ProfileProviderBalance`, drop it

**Files:**
- Modify: `backend/src/db/models.py` (`init_db()` migration block — same place as the `chrome_port`/`wallet_address` migrations)
- Test: `backend/tests/db/test_account_migration.py`

The migration must: create the new tables (handled by `create_all`), add `Profile.kind` + `Bet.account_id` columns to existing tables, convert each `ProfileProviderBalance` row to an `Account` + `ProfileAccount` (sharp providers collapse to ONE shared account), backfill `bets.account_id`, then drop `profile_provider_balances`. Idempotent.

- [ ] **Step 1: Write the failing test (seed an old-shape DB, run migration, assert)**

```python
# backend/tests/db/test_account_migration.py
"""Verifies migrate_to_accounts() converts ProfileProviderBalance correctly."""
import sqlite3

from backend.src.db.models import migrate_to_accounts


def _seed_old_db(path):
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE profiles (id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER);
        CREATE TABLE providers (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE profile_provider_balances
            (id INTEGER PRIMARY KEY, profile_id INTEGER, provider_id TEXT,
             balance REAL, account_opened_at TEXT);
        CREATE TABLE bets (id INTEGER PRIMARY KEY, profile_id INTEGER, provider_id TEXT);
        INSERT INTO profiles VALUES (1,'edge',1),(2,'campaign',0);
        INSERT INTO providers VALUES ('polymarket','Poly'),('betinia','Betinia');
        -- both profiles hold the same sharp account; campaign also has a soft book
        INSERT INTO profile_provider_balances VALUES
            (1,1,'polymarket',76.29,NULL),
            (2,2,'polymarket',76.29,NULL),
            (2,2,'betinia',500.0,NULL);
        INSERT INTO bets VALUES (10,1,'polymarket'),(11,2,'betinia');
        """
    )
    # new columns/tables the migration expects to exist (create_all would add these)
    c.executescript(
        """
        ALTER TABLE profiles ADD COLUMN kind TEXT DEFAULT 'edge';
        ALTER TABLE bets ADD COLUMN account_id INTEGER;
        CREATE TABLE accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, provider_id TEXT,
            label TEXT, kind TEXT, balance REAL, currency TEXT, account_opened_at TEXT,
            is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT);
        CREATE TABLE profile_accounts (profile_id INTEGER, account_id INTEGER,
            created_at TEXT, PRIMARY KEY (profile_id, account_id));
        """
    )
    c.commit()
    return c


def test_sharp_collapses_to_one_shared_account(tmp_path):
    db = str(tmp_path / "t.db")
    c = _seed_old_db(db)
    migrate_to_accounts(c, unlimited_providers={"polymarket", "kalshi", "pinnacle", "cloudbet"})
    # exactly ONE polymarket account, kind sharp, balance from edge profile
    rows = c.execute("SELECT id,label,kind,balance FROM accounts WHERE provider_id='polymarket'").fetchall()
    assert len(rows) == 1
    poly_id, label, kind, bal = rows[0]
    assert kind == "sharp" and label == "rasmus" and abs(bal - 76.29) < 1e-6
    # linked to BOTH profiles
    links = c.execute("SELECT profile_id FROM profile_accounts WHERE account_id=?", (poly_id,)).fetchall()
    assert {r[0] for r in links} == {1, 2}
    # betinia is a per-campaign soft account, single-linked to profile 2
    brow = c.execute("SELECT id,kind FROM accounts WHERE provider_id='betinia'").fetchone()
    assert brow[1] == "soft"
    blinks = c.execute("SELECT profile_id FROM profile_accounts WHERE account_id=?", (brow[0],)).fetchall()
    assert {r[0] for r in blinks} == {2}


def test_bets_backfilled_and_idempotent(tmp_path):
    db = str(tmp_path / "t.db")
    c = _seed_old_db(db)
    migrate_to_accounts(c, unlimited_providers={"polymarket"})
    poly = c.execute("SELECT id FROM accounts WHERE provider_id='polymarket'").fetchone()[0]
    betinia = c.execute("SELECT id FROM accounts WHERE provider_id='betinia'").fetchone()[0]
    assert c.execute("SELECT account_id FROM bets WHERE id=10").fetchone()[0] == poly
    assert c.execute("SELECT account_id FROM bets WHERE id=11").fetchone()[0] == betinia
    # second run is a no-op (no duplicate accounts/links)
    migrate_to_accounts(c, unlimited_providers={"polymarket"})
    assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM profile_accounts").fetchone()[0] == 3
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && pytest tests/db/test_account_migration.py -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_to_accounts'`.

- [ ] **Step 3: Implement `migrate_to_accounts(conn, unlimited_providers)`**

Add to `backend/src/db/models.py` (module-level function; the in-`init_db` block
will call it with the real connection). Idempotent via existence probes.

```python
def migrate_to_accounts(conn, unlimited_providers: set[str]) -> None:
    """Convert profile_provider_balances → accounts + profile_accounts (idempotent).

    Sharp providers (unlimited_providers) collapse to ONE shared account, balance
    taken from the active (or lowest-id) profile, linked to every profile that
    held a balance row. Other providers become per-profile soft accounts. Backfills
    bets.account_id. Caller is responsible for dropping profile_provider_balances
    AFTER verifying (separate step) — this function does not drop it.
    """
    cur = conn.cursor()
    # Skip entirely if the old table is already gone (migration completed earlier).
    try:
        cur.execute("SELECT 1 FROM profile_provider_balances LIMIT 1")
    except Exception:
        return

    # Default existing profiles to kind='edge' (column already added by create/ALTER).
    cur.execute("UPDATE profiles SET kind='edge' WHERE kind IS NULL")

    # Determine the 'truth' profile for shared sharp balances: active first, else min id.
    row = cur.execute("SELECT id FROM profiles WHERE is_active=1 ORDER BY id LIMIT 1").fetchone()
    truth_pid = row[0] if row else (cur.execute("SELECT MIN(id) FROM profiles").fetchone() or [None])[0]

    ppb = cur.execute(
        "SELECT profile_id, provider_id, balance, account_opened_at FROM profile_provider_balances"
    ).fetchall()

    def _account_id_for(provider_id):
        r = cur.execute(
            "SELECT id FROM accounts WHERE provider_id=? AND label='rasmus'", (provider_id,)
        ).fetchone()
        return r[0] if r else None

    # 1) Sharp: one shared account per provider.
    sharp_providers = {pid for (_, pid, _, _) in ppb if pid in unlimited_providers}
    for pid in sharp_providers:
        if _account_id_for(pid):
            continue  # already migrated
        # balance from truth profile if present, else any row for this provider
        bal_row = cur.execute(
            "SELECT balance, account_opened_at FROM profile_provider_balances "
            "WHERE provider_id=? AND profile_id=?",
            (pid, truth_pid),
        ).fetchone() or cur.execute(
            "SELECT balance, account_opened_at FROM profile_provider_balances WHERE provider_id=?",
            (pid,),
        ).fetchone()
        bal, opened = (bal_row or (0.0, None))
        cur.execute(
            "INSERT INTO accounts (provider_id,label,kind,balance,currency,account_opened_at,is_active) "
            "VALUES (?,?,?,?,?,?,1)",
            (pid, "rasmus", "sharp", bal, _currency_for_provider(pid), opened),
        )

    # 2) Soft: per-profile account labeled from profile name.
    for prof_id, prov_id, balance, opened in ppb:
        if prov_id in unlimited_providers:
            continue
        # label = profile name (fallback to profile id)
        nm = cur.execute("SELECT name FROM profiles WHERE id=?", (prof_id,)).fetchone()
        label = (nm[0] if nm and nm[0] else f"p{prof_id}")
        exists = cur.execute(
            "SELECT id FROM accounts WHERE provider_id=? AND label=?", (prov_id, label)
        ).fetchone()
        if not exists:
            cur.execute(
                "INSERT INTO accounts (provider_id,label,kind,balance,currency,account_opened_at,is_active) "
                "VALUES (?,?,?,?,?,?,1)",
                (prov_id, label, "soft", balance, _currency_for_provider(prov_id), opened),
            )

    # 3) Links: one row per (profile, account) the profile held a balance for.
    for prof_id, prov_id, _balance, _opened in ppb:
        if prov_id in unlimited_providers:
            acct_id = _account_id_for(prov_id)
        else:
            nm = cur.execute("SELECT name FROM profiles WHERE id=?", (prof_id,)).fetchone()
            label = (nm[0] if nm and nm[0] else f"p{prof_id}")
            r = cur.execute(
                "SELECT id FROM accounts WHERE provider_id=? AND label=?", (prov_id, label)
            ).fetchone()
            acct_id = r[0] if r else None
        if acct_id and not cur.execute(
            "SELECT 1 FROM profile_accounts WHERE profile_id=? AND account_id=?", (prof_id, acct_id)
        ).fetchone():
            cur.execute(
                "INSERT INTO profile_accounts (profile_id, account_id) VALUES (?,?)", (prof_id, acct_id)
            )

    # 4) Backfill bets.account_id via (profile_id, provider_id) → linked account.
    cur.execute(
        """
        UPDATE bets SET account_id = (
            SELECT pa.account_id FROM profile_accounts pa
            JOIN accounts a ON a.id = pa.account_id
            WHERE pa.profile_id = bets.profile_id AND a.provider_id = bets.provider_id
            LIMIT 1
        )
        WHERE account_id IS NULL
        """
    )
    conn.commit()
```

Add the currency helper near the function (reuse the project's provider-currency
source; if a `get_provider_currency` exists in `..config`, import and use it —
otherwise map via the SEK/USD/USDC sets used elsewhere):

```python
def _currency_for_provider(provider_id: str) -> str:
    """Native currency for a provider (USDC/USD/SEK). Mirrors providers.yaml."""
    from ..config import get_provider_currency  # existing helper
    try:
        return get_provider_currency(provider_id) or "SEK"
    except Exception:
        return "SEK"
```

> If `get_provider_currency` does not exist, the implementer must check
> `backend/src/config/__init__.py` for the actual exported name (the spec's
> "existing provider currency resolution") and use it; do not invent a new map.

- [ ] **Step 4: Wire it into `init_db()` + add the column ALTERs + drop**

In the `init_db()` migration block (where `chrome_port` etc. are added), after
`create_all`, add guarded ALTERs and the call. Match the existing probe pattern:

```python
        # Add kind to profiles (edge/bonus ROI bucketing)
        try:
            cursor.execute("SELECT kind FROM profiles LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE profiles ADD COLUMN kind TEXT DEFAULT 'edge'")
            raw.commit()
        # Add account_id to bets
        try:
            cursor.execute("SELECT account_id FROM bets LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE bets ADD COLUMN account_id INTEGER")
            raw.commit()
        # Backfill accounts from profile_provider_balances (idempotent), then drop it
        try:
            from ..constants import UNLIMITED_PROVIDERS
            migrate_to_accounts(raw, set(UNLIMITED_PROVIDERS))
            # Verify reconciliation before dropping: every old row maps to a link
            ppb_n = cursor.execute("SELECT COUNT(*) FROM profile_provider_balances").fetchone()[0]
            link_n = cursor.execute("SELECT COUNT(*) FROM profile_accounts").fetchone()[0]
            if ppb_n > 0 and link_n >= ppb_n:
                cursor.execute("DROP TABLE profile_provider_balances")
                raw.commit()
        except sqlite3.OperationalError:
            pass  # table already dropped on a prior run
```

> Note for Postgres (production): `raw`/`cursor` here is whatever the existing
> `init_db` block uses. The existing migrations in this file already run against
> the production Postgres via this same block — follow their exact connection
> handling. The `ALTER TABLE ... ADD COLUMN` + `DROP TABLE` statements are valid
> on both sqlite and Postgres. Keep `ON DELETE SET NULL` defined in the model so
> `create_all` emits it on Postgres.

- [ ] **Step 5: Run migration test + full model test**

Run: `cd backend && pytest tests/db/test_account_migration.py tests/db/test_account_models.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add backend/src/db/models.py backend/tests/db/test_account_migration.py
git commit -m "feat(db): migrate profile_provider_balances -> accounts + profile_accounts"
```

---

## Task 3: `AccountRepo`

**Files:**
- Create: `backend/src/repositories/account_repo.py`
- Modify: `backend/src/repositories/__init__.py`
- Test: `backend/tests/repositories/test_account_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/repositories/test_account_repo.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.src.db.models import Base, Profile, Provider
from backend.src.repositories.account_repo import AccountRepo


def _repo():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add_all([Provider(id="polymarket", name="P"), Provider(id="betinia", name="B")])
    a = Profile(name="edge", kind="edge", is_active=True)
    b = Profile(name="camp", kind="bonus")
    s.add_all([a, b]); s.flush()
    return AccountRepo(s), a, b


def test_shared_sharp_resolves_same_account_across_profiles():
    repo, edge, camp = _repo()
    acct = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    repo.link(edge.id, acct.id)
    repo.link(camp.id, acct.id)
    repo.db.flush()
    assert repo.resolve(edge.id, "polymarket").id == repo.resolve(camp.id, "polymarket").id


def test_set_balance_is_shared():
    repo, edge, camp = _repo()
    acct = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    repo.link(edge.id, acct.id); repo.link(camp.id, acct.id); repo.db.flush()
    repo.set_balance(acct.id, 80.0); repo.db.flush()
    assert repo.resolve(camp.id, "polymarket").balance == 80.0


def test_distinct_accounts_dedupes_shared():
    repo, edge, camp = _repo()
    acct = repo.get_or_create(provider_id="polymarket", label="rasmus", kind="sharp", currency="USDC")
    repo.link(edge.id, acct.id); repo.link(camp.id, acct.id); repo.db.flush()
    assert len(repo.distinct_accounts()) == 1


def test_fresh_account_not_visible_to_other_profile():
    repo, edge, camp = _repo()
    fresh = repo.get_or_create(provider_id="polymarket", label="alt2", kind="sharp", currency="USDC")
    repo.link(camp.id, fresh.id); repo.db.flush()
    assert repo.resolve(edge.id, "polymarket") is None
    assert repo.resolve(camp.id, "polymarket").label == "alt2"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && pytest tests/repositories/test_account_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: ...account_repo`.

- [ ] **Step 3: Implement `AccountRepo`**

```python
# backend/src/repositories/account_repo.py
"""Account repository — shared/labeled real accounts + per-profile visibility."""
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..db.models import Account, ProfileAccount


class AccountRepo:
    """Data access for accounts and profile→account links."""

    def __init__(self, db: Session):
        self.db = db

    def get(self, account_id: int) -> Account | None:
        return self.db.query(Account).filter(Account.id == account_id).first()

    def get_or_create(self, provider_id: str, label: str, kind: str, currency: str) -> Account:
        acct = (
            self.db.query(Account)
            .filter(Account.provider_id == provider_id, Account.label == label)
            .first()
        )
        if acct:
            return acct
        acct = Account(provider_id=provider_id, label=label, kind=kind, currency=currency, is_active=True)
        self.db.add(acct)
        self.db.flush()
        return acct

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

    def accounts_for_profile(self, profile_id: int) -> list[Account]:
        return (
            self.db.query(Account)
            .join(ProfileAccount, ProfileAccount.account_id == Account.id)
            .filter(ProfileAccount.profile_id == profile_id, Account.is_active)
            .all()
        )

    def distinct_accounts(self) -> list[Account]:
        """All active accounts (each once) — for cross-profile grand totals."""
        return self.db.query(Account).filter(Account.is_active).all()

    def resolve(self, profile_id: int, provider_id: str) -> Account | None:
        """The single active account this profile uses for a provider, or None."""
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

    def set_balance(self, account_id: int, balance: float) -> None:
        acct = self.get(account_id)
        if acct:
            acct.balance = balance
            acct.updated_at = datetime.now(UTC)

    def link_count(self, account_id: int) -> int:
        return self.db.query(ProfileAccount).filter(ProfileAccount.account_id == account_id).count()

    def has_bets(self, account_id: int) -> bool:
        from ..db.models import Bet
        return self.db.query(Bet.id).filter(Bet.account_id == account_id).first() is not None
```

Export it in `backend/src/repositories/__init__.py`:

```python
from .account_repo import AccountRepo
```
and add `"AccountRepo"` to `__all__`.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd backend && pytest tests/repositories/test_account_repo.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/repositories/account_repo.py backend/src/repositories/__init__.py backend/tests/repositories/test_account_repo.py
git commit -m "feat(repo): AccountRepo with shared-account resolver and link queries"
```

---

## Task 4: Re-point `ProfileRepo` balance methods through accounts

**Files:**
- Modify: `backend/src/repositories/profile_repo.py`
- Test: `backend/tests/repositories/test_profile_balance_shared.py`

Keep every method signature identical. Change the bodies to resolve via
`AccountRepo` so the existing ~15 call sites and hot paths are untouched, but sharp
balances become shared.

- [ ] **Step 1: Write the failing test (shared semantics through ProfileRepo)**

```python
# backend/tests/repositories/test_profile_balance_shared.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.src.db.models import Base, Profile, Provider
from backend.src.repositories.account_repo import AccountRepo
from backend.src.repositories.profile_repo import ProfileRepo


def _setup():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add(Provider(id="polymarket", name="P"))
    edge = Profile(name="edge", kind="edge", is_active=True)
    camp = Profile(name="camp", kind="bonus")
    s.add_all([edge, camp]); s.flush()
    ar = AccountRepo(s)
    acct = ar.get_or_create("polymarket", "rasmus", "sharp", "USDC")
    ar.link(edge.id, acct.id); ar.link(camp.id, acct.id); s.flush()
    return ProfileRepo(s), edge, camp


def test_set_balance_in_one_profile_visible_in_other():
    pr, edge, camp = _setup()
    pr.set_balance(edge.id, "polymarket", 90.0)
    pr.db.commit()
    assert pr.get_balance(camp.id, "polymarket") == 90.0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && pytest tests/repositories/test_profile_balance_shared.py -v`
Expected: FAIL — current `set_balance` writes per-profile `ProfileProviderBalance`, so the second profile reads 0.0 (or errors because the table is gone).

- [ ] **Step 3: Rewrite the balance methods to use `AccountRepo`**

In `profile_repo.py`, replace the `ProfileProviderBalance` imports with `AccountRepo`
usage and rewrite the balance section. Construct an `AccountRepo(self.db)` lazily.

```python
# at top, replace the ProfileProviderBalance import
from ..db.models import BONUS_MIN_ODDS, Account, Profile, ProfileAccount, ProfileProviderBonus

# add inside __init__:
        self._accounts = AccountRepo(db)
```
(import `from .account_repo import AccountRepo` at top.)

```python
    def get_balance(self, profile_id: int, provider_id: str) -> float:
        acct = self._accounts.resolve(profile_id, provider_id)
        return acct.balance if acct else 0.0

    def set_balance(self, profile_id: int, provider_id: str, balance: float) -> None:
        acct = self._accounts.resolve(profile_id, provider_id)
        if acct:
            self._accounts.set_balance(acct.id, balance)
        # If no linked account exists yet, no-op: account creation is owned by the
        # profile-create / fresh-account flow (AccountService), not balance sync.

    def adjust_balance(self, profile_id: int, provider_id: str, amount: float) -> float:
        acct = self._accounts.resolve(profile_id, provider_id)
        if not acct:
            return 0.0
        acct.balance = (acct.balance or 0.0) + amount
        return acct.balance
```

Rewrite the aggregate/listing methods to iterate `accounts_for_profile`:

```python
    def get_total_bankroll(self, profile_id: int) -> float:
        now = time.time()
        cached = _bankroll_cache.get(profile_id)
        if cached and now < cached[0]:
            return cached[1]
        from ..config import get_exchange_rate
        accts = self._accounts.accounts_for_profile(profile_id)
        total = sum((a.balance or 0.0) * get_exchange_rate(a.provider_id) for a in accts)
        _bankroll_cache[profile_id] = (now + _BANKROLL_CACHE_TTL, total)
        return total

    def get_stake_bankroll(self, profile_id: int) -> float:
        now = time.time()
        cached = _stake_bankroll_cache.get(profile_id)
        if cached and now < cached[0]:
            return cached[1]
        from ..config import get_exchange_rate
        from ..constants import UNLIMITED_PROVIDERS
        accts = self._accounts.accounts_for_profile(profile_id)
        total = sum(
            (a.balance or 0.0) * get_exchange_rate(a.provider_id)
            for a in accts if a.provider_id in UNLIMITED_PROVIDERS
        )
        _stake_bankroll_cache[profile_id] = (now + _BANKROLL_CACHE_TTL, total)
        return total

    def get_all_balances(self, profile_id: int) -> dict[str, float]:
        return {
            a.provider_id: a.balance
            for a in self._accounts.accounts_for_profile(profile_id)
            if (a.balance or 0.0) > 0
        }

    def get_all_registered_providers(self, profile_id: int) -> set[str]:
        return {a.provider_id for a in self._accounts.accounts_for_profile(profile_id)}
```

Remove `copy_balances` (its responsibility moves to `AccountService` in Task 7).
Grep for callers first: `git grep -n copy_balances backend/ local/` — the only
caller is the profile-create path, which Task 7 replaces. If any other caller
exists, leave a thin shim that delegates to `AccountService.link_shared_sharp`.

- [ ] **Step 4: Run the test + existing profile_repo tests**

Run: `cd backend && pytest tests/repositories/test_profile_balance_shared.py -v && pytest tests/ -k "profile or bankroll" -v`
Expected: new test PASS; pre-existing balance/bankroll tests still PASS (or updated if they asserted `ProfileProviderBalance` directly — update those to the new model).

- [ ] **Step 5: Commit**

```bash
git add backend/src/repositories/profile_repo.py backend/tests/repositories/test_profile_balance_shared.py
git commit -m "refactor(repo): ProfileRepo balances resolve through shared accounts"
```

---

## Task 5: `get_bankroll` returns labels; `set/{provider_id}` route unchanged

**Files:**
- Modify: `backend/src/services/bankroll_service.py` (`get_bankroll`)
- Test: `backend/tests/services/test_bankroll_labels.py`

The `POST /api/bankroll/set/{provider_id}` route already calls
`profile_repo.set_balance(profile.id, provider_id, balance)` → now shared (Task 4),
no route change needed. We only add `label` + `account_id` to the `get_bankroll`
per-provider payload.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_bankroll_labels.py
# Build a profile with a linked, labeled sharp account, call get_bankroll(),
# assert the provider entry carries label="rasmus" and an account_id.
# (Mirror the existing bankroll_service test setup; assert the new keys.)
```
> Implementer: copy the setup from the nearest existing `bankroll_service`
> test; the new assertions are `entry["label"] == "rasmus"` and
> `entry["account_id"] is not None` for the linked provider.

- [ ] **Step 2: Run it to confirm it fails** — `KeyError: 'label'`.

- [ ] **Step 3: Add label/account_id in `get_bankroll`**

In `bankroll_service.py` `get_bankroll`, when building each provider's dict, resolve
the account and include its label + id. Use `AccountRepo(self.db).resolve(profile.id, provider_id)`.
Add `"label": acct.label if acct else None` and `"account_id": acct.id if acct else None`
to the per-provider entry. Preserve every existing key (balance, balance_sek,
currency, exchange_rate_sek, bonus_trigger_amount).

- [ ] **Step 4: Run the test** — PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/services/bankroll_service.py backend/tests/services/test_bankroll_labels.py
git commit -m "feat(bankroll): expose account label + id in get_bankroll"
```

---

## Task 6: Stamp `account_id` on recorded bets

**Files:**
- Modify: `backend/src/api/schemas.py` (`BetCreate` + `account_id`, `BatchBetLeg` + `account_id`)
- Modify: `backend/src/repositories/bet_repo.py` (`create` already `**kwargs` — passes through; no change unless it filters)
- Modify: `local/mirror/play_loop.py` (`_record_manual_bet`), `local/mirror/pending_loop.py` (`_record_unknown_open_bets`)
- Test: `backend/tests/services/test_bet_account_stamp.py`

> The recorders run in the LOCAL client and POST to the API. The cleanest seam is
> server-side: resolve `account_id` from `(active profile, provider_id)` at bet
> insert time in the API bet-create handler, so both local recorders and any
> direct API caller get it for free. Prefer this over editing each local recorder.

- [ ] **Step 1: Write the failing test (server-side stamp on create)**

```python
# backend/tests/services/test_bet_account_stamp.py
# Given an active profile linked to a polymarket account, when a bet is created
# for provider polymarket WITHOUT an explicit account_id, the persisted Bet has
# account_id == that account's id.
```
> Implementer: locate the bet-create service/route (search `bet_repo.create` and
> `BetCreate` usage in `backend/src/api/routes` / `services`). Assert the stamp.

- [ ] **Step 2: Run it to confirm it fails** — `account_id is None`.

- [ ] **Step 3: Implement**

- Add `account_id: int | None = None` to `BetCreate` and `BatchBetLeg` in `schemas.py`.
- In the bet-create handler (where `BetRepo.create(**...)` is called), before
  creating: if `account_id` is not provided, set
  `account_id = AccountRepo(db).resolve(profile.id, provider_id)` (`.id` if found).
  Do NOT change dedup keys, the no-fallback stake rule, or balance-check logic
  (CLAUDE.md mirror invariants) — only inject the resolved `account_id`.
- `BetRepo.create(**kwargs)` already forwards kwargs to `Bet(...)`; confirm it
  doesn't strip unknown keys. No change expected.

- [ ] **Step 4: Run the test + mirror-related backend tests** — PASS; existing
  recorder/dedup tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/src/api/schemas.py backend/src/api/routes backend/tests/services/test_bet_account_stamp.py
git commit -m "feat(bets): stamp account_id at create from active profile+provider"
```

---

## Task 7: Profile-create wiring (`AccountService`) + delete GC

**Files:**
- Create: `backend/src/services/account_service.py`
- Modify: `backend/src/api/schemas.py` (`ProfileCreate` + `kind`, `use_shared_sharp`, `fresh_sharp_label`, `soft_providers`)
- Modify: `backend/src/api/routes/profiles.py` (create + delete routes)
- Test: `backend/tests/services/test_account_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_account_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.src.db.models import Base, Profile, Provider, Account, ProfileAccount, Bet
from backend.src.repositories.account_repo import AccountRepo
from backend.src.services.account_service import AccountService


def _svc():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = Session(eng)
    s.add_all([Provider(id="polymarket", name="P"), Provider(id="pinnacle", name="Pin"),
               Provider(id="betinia", name="B")])
    edge = Profile(name="edge", kind="edge", is_active=True)
    s.add(edge); s.flush()
    ar = AccountRepo(s)
    for prov in ("polymarket", "pinnacle"):
        a = ar.get_or_create(prov, "rasmus", "sharp", "USDC")
        ar.link(edge.id, a.id)
    s.flush()
    return AccountService(s), s, edge


def test_use_shared_sharp_links_existing_no_new_rows():
    svc, s, edge = _svc()
    before = s.query(Account).filter(Account.kind == "sharp").count()
    camp = Profile(name="camp", kind="bonus"); s.add(camp); s.flush()
    svc.provision(camp, use_shared_sharp=True, fresh_sharp_label=None, soft_providers=["betinia"])
    s.flush()
    after = s.query(Account).filter(Account.kind == "sharp").count()
    assert after == before  # no new sharp accounts
    # camp sees the SAME shared poly account as edge
    ar = AccountRepo(s)
    assert ar.resolve(camp.id, "polymarket").id == ar.resolve(edge.id, "polymarket").id
    # and got its own soft betinia account
    assert ar.resolve(camp.id, "betinia").kind == "soft"


def test_fresh_sharp_creates_isolated_accounts():
    svc, s, edge = _svc()
    camp = Profile(name="camp", kind="bonus"); s.add(camp); s.flush()
    svc.provision(camp, use_shared_sharp=False, fresh_sharp_label="alt2", soft_providers=[])
    s.flush()
    ar = AccountRepo(s)
    assert ar.resolve(camp.id, "polymarket").label == "alt2"
    assert ar.resolve(edge.id, "polymarket").label == "rasmus"  # unchanged, not visible to each other


def test_delete_gc_keeps_shared_softdeletes_with_bets():
    svc, s, edge = _svc()
    camp = Profile(name="camp", kind="bonus"); s.add(camp); s.flush()
    svc.provision(camp, use_shared_sharp=True, fresh_sharp_label=None, soft_providers=["betinia"])
    s.flush()
    ar = AccountRepo(s)
    betinia = ar.resolve(camp.id, "betinia")
    s.add(Bet(profile_id=camp.id, provider_id="betinia", account_id=betinia.id,
              odds=2.0, stake=10.0, currency="SEK", result="won", payout=20.0))
    s.flush()
    poly = ar.resolve(camp.id, "polymarket")
    svc.delete_profile_accounts(camp)
    s.flush()
    # shared poly survives (still linked to edge)
    assert s.query(Account).get(poly.id).is_active is True
    # betinia has bets -> soft-deleted, not removed
    assert s.query(Account).get(betinia.id).is_active is False
```

- [ ] **Step 2: Run it to confirm it fails** — `ModuleNotFoundError: account_service`.

- [ ] **Step 3: Implement `AccountService`**

```python
# backend/src/services/account_service.py
"""Profile-create account provisioning + delete-time GC."""
from sqlalchemy.orm import Session

from ..constants import UNLIMITED_PROVIDERS
from ..db.models import Account, Profile, ProfileAccount
from ..repositories.account_repo import AccountRepo


class AccountService:
    def __init__(self, db: Session):
        self.db = db
        self.accounts = AccountRepo(db)

    def _edge_profile(self) -> Profile | None:
        return (
            self.db.query(Profile)
            .filter(Profile.kind == "edge")
            .order_by(Profile.is_active.desc(), Profile.id)
            .first()
        )

    def link_shared_sharp(self, profile: Profile) -> None:
        """Link the existing shared sharp accounts (those of the edge profile)."""
        edge = self._edge_profile()
        if not edge or edge.id == profile.id:
            return
        for acct in self.accounts.accounts_for_profile(edge.id):
            if acct.provider_id in UNLIMITED_PROVIDERS and acct.kind == "sharp":
                self.accounts.link(profile.id, acct.id)

    def create_fresh_sharp(self, profile: Profile, label: str) -> None:
        for prov in UNLIMITED_PROVIDERS:
            from ..db.models import _currency_for_provider
            acct = self.accounts.get_or_create(prov, label, "sharp", _currency_for_provider(prov))
            self.accounts.link(profile.id, acct.id)

    def create_soft(self, profile: Profile, providers: list[str]) -> None:
        from ..db.models import _currency_for_provider
        for prov in providers:
            label = profile.name
            acct = self.accounts.get_or_create(prov, label, "soft", _currency_for_provider(prov))
            self.accounts.link(profile.id, acct.id)

    def provision(self, profile: Profile, *, use_shared_sharp: bool,
                  fresh_sharp_label: str | None, soft_providers: list[str]) -> None:
        if use_shared_sharp:
            self.link_shared_sharp(profile)
        elif fresh_sharp_label:
            self.create_fresh_sharp(profile, fresh_sharp_label)
        if soft_providers:
            self.create_soft(profile, soft_providers)

    def delete_profile_accounts(self, profile: Profile) -> None:
        """Unlink this profile's accounts; GC orphans (hard-delete if bet-less,
        else soft-delete)."""
        links = self.db.query(ProfileAccount).filter(ProfileAccount.profile_id == profile.id).all()
        acct_ids = [link.account_id for link in links]
        for link in links:
            self.db.delete(link)
        self.db.flush()
        for aid in acct_ids:
            if self.accounts.link_count(aid) > 0:
                continue  # still shared
            if self.accounts.has_bets(aid):
                acct = self.accounts.get(aid)
                if acct:
                    acct.is_active = False
            else:
                acct = self.accounts.get(aid)
                if acct:
                    self.db.delete(acct)
```

- [ ] **Step 4: Extend schemas + wire routes**

In `schemas.py` `ProfileCreate`, add:

```python
    kind: str | None = "edge"  # "edge" | "bonus"
    use_shared_sharp: bool | None = True
    fresh_sharp_label: str | None = None
    soft_providers: list[str] | None = None  # soft books this campaign signs up for
```

In `profiles.py` create route: after the `Profile` row is created + flushed, set
`profile.kind = data.kind or "edge"` and call
`AccountService(db).provision(profile, use_shared_sharp=bool(data.use_shared_sharp),
fresh_sharp_label=data.fresh_sharp_label, soft_providers=data.soft_providers or [])`
then `db.commit()`. Replace any old `copy_balances` call. Include `kind` in
`profile_to_dict`.

In `profiles.py` delete route: call `AccountService(db).delete_profile_accounts(profile)`
before deleting the profile row (so links are cleaned + GC runs).

- [ ] **Step 5: Run tests**

Run: `cd backend && pytest tests/services/test_account_service.py -v && pytest tests/ -k profile -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/services/account_service.py backend/src/api/schemas.py backend/src/api/routes/profiles.py backend/tests/services/test_account_service.py
git commit -m "feat(profiles): provision shared/fresh sharp + soft accounts on create; GC on delete"
```

---

## Task 8: Stats — Rule-B ROI exclusion + separate `bonus_profit`

**Files:**
- Modify: `backend/src/repositories/bet_repo.py` (`get_settled_aggregates` carries `kind`; add `get_bonus_profit_aggregates_all_profiles`)
- Modify: `backend/src/services/bankroll_service.py` (`get_stats`)
- Test: `backend/tests/services/test_stats_rule_b.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/services/test_stats_rule_b.py
# Setup: edge profile with one winning value bet (stake 100 @ 2.0, payout 200).
#        bonus profile with a sharp HEDGE leg (real money, kind=bonus profile)
#        that won: stake 1290 @ 1.55 payout ~2000.  (is_bonus False — it's the
#        real hedge leg, classified bonus ONLY via profile.kind.)
# Assert via get_stats():
#   - roi denominator excludes the bonus-profile hedge leg
#     (total_staked == 100, not 1390)
#   - total_profit/roi reflects ONLY the edge bet (profit 100, roi 100%)
#   - bonus_profit > 0 and includes the hedge leg's profit
#   - flipping the hedge leg won->lost does NOT change roi_pct (Rule B proof)
```
> Implementer: build with the in-memory Session pattern used by other service
> tests; activate the edge profile (get_stats is active-profile scoped for ROI).

- [ ] **Step 2: Run it to confirm it fails** — current `get_stats` would count the
hedge leg (it's `is_bonus=False`) and inflate `total_staked`/`roi`.

- [ ] **Step 3: Implement**

In `bet_repo.py`, extend `get_settled_aggregates` to join `Profile` and carry
`Profile.kind` in the grouping/labels:

```python
    def get_settled_aggregates(self, profile_id: int) -> list:
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
            .filter(Bet.result != "pending", Bet.profile_id == profile_id)
            .group_by(Bet.provider_id, Bet.currency, Bet.result, Bet.is_bonus, Profile.kind)
            .all()
        )
```

Add an all-profiles bonus aggregate (for the all-profiles `bonus_profit` decision):

```python
    def get_bonus_profit_aggregates(self) -> list:
        """Settled bets across ALL bonus-kind profiles, grouped for SEK conversion.

        Bonus profit = profit of every bet under a bonus profile (both the soft
        free-bet leg and the sharp hedge leg), kept out of true ROI.
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
            .filter(Bet.result != "pending", Profile.kind == "bonus")
            .group_by(Bet.provider_id, Bet.currency, Bet.result, Bet.is_bonus)
            .all()
        )
```

In `bankroll_service.py` `get_stats`: the active-profile ROI rows must now also
exclude `kind == 'bonus'`:

```python
        # Rule B: true ROI counts only genuine edge bets — exclude bonus capital
        # (is_bonus) AND every bet placed under a bonus-campaign profile (both
        # the soft free-bet leg and its real-money sharp hedge leg).
        real_rows = [r for r in rows if not r.is_bonus and r.kind == "edge"]
```

Then compute all-profiles bonus profit and put it in the response:

```python
        bonus_rows = self.bet_repo.get_bonus_profit_aggregates()
        bonus_profit = sum(
            to_sek(row_profit(r), r.provider_id, r.currency) for r in bonus_rows
        )
        # ... in the returned dict:
        "bonus_profit": round(bonus_profit, 2),
```

(Keep `freebet_profit` as-is or fold into `bonus_profit`; do not let either enter
`roi_pct`/`total_profit`/`total_staked`.)

- [ ] **Step 4: Run the test + existing stats tests**

Run: `cd backend && pytest tests/services/test_stats_rule_b.py -v && pytest tests/ -k stats -v`
Expected: PASS; ROI unchanged by hedge-leg result; `bonus_profit` populated.

- [ ] **Step 5: Commit**

```bash
git add backend/src/repositories/bet_repo.py backend/src/services/bankroll_service.py backend/tests/services/test_stats_rule_b.py
git commit -m "feat(stats): Rule-B ROI excludes bonus-profile legs; separate bonus_profit total"
```

---

## Task 9: Frontend — create form (purpose + sharp choice + label) and labeled balances

**Files:**
- Modify: `frontend/src/types/index.ts` (`Profile.kind`; `ProfileCreate` new fields)
- Modify: `frontend/src/services/api/profiles.ts` (no change — already posts `ProfileCreate`; verify)
- Modify: `frontend/src/hooks/useProfiles.ts` (`createProfile` accepts a full `ProfileCreate`, not just `name`)
- Modify: `frontend/src/components/ProfileSelector.tsx` (the create form)
- Modify: `frontend/src/pages/BankrollPage.tsx` (render `PROVIDER (label)`)
- Modify: `frontend/src/pages/StatsPage.tsx` (render `bonus_profit` distinct from ROI)

- [ ] **Step 1: Extend types**

In `frontend/src/types/index.ts`:
```typescript
// Profile interface: add
  kind: 'edge' | 'bonus';
// ProfileCreate interface: add
  kind?: 'edge' | 'bonus';
  use_shared_sharp?: boolean;
  fresh_sharp_label?: string | null;
  soft_providers?: string[];
```

- [ ] **Step 2: Widen `createProfile`**

In `useProfiles.ts`, change `createProfile(name: string)` to
`createProfile(data: ProfileCreate)` and forward it to `profilesApi.createProfile(data)`.
Update the call in `ProfileSelector.tsx` accordingly.

- [ ] **Step 3: Build the create form**

In `ProfileSelector.tsx`, replace the bare name input with the form from spec
§"Creating a profile": name input, Purpose radio (Edge / Bonus campaign → `kind`),
Sharp-accounts radio (Use my sharp accounts → `use_shared_sharp=true` / Create
fresh + label text input → `use_shared_sharp=false, fresh_sharp_label=<label>`).
On submit build the `ProfileCreate` object and call `createProfile(obj)`. Keep the
existing retro/terminal styling and the amber/green class conventions already in
the file.

- [ ] **Step 4: Labeled balances + bonus profit**

- `BankrollPage.tsx`: where each provider balance renders, show
  `${provider.toUpperCase()} (${label})` when `label` is present (the API now
  returns `label`). The shared sharp account shows e.g. `POLYMARKET (rasmus)`.
- `StatsPage.tsx`: render `stats.bonus_profit` as its own line/stat (e.g. a
  "Bonus profit" tile) visually separate from ROI/Profit. Do not add it into the
  ROI or total-profit numbers.

- [ ] **Step 5: Lint + manual verify**

Run: `cd frontend && npm run lint`
Expected: clean.

Manual (Claude Preview / `preview_screenshot`, app started via `local\betty.bat`):
- Create a **bonus** profile with **Use my sharp accounts** → its Bankroll shows
  `POLYMARKET (rasmus)` with the SAME balance as the edge profile; set the balance
  under one profile and confirm it mirrors in the other.
- Create a profile with **fresh sharp** label `alt2` → shows `POLYMARKET (alt2)`,
  independent; not visible to other profiles.
- Stats shows true ROI unchanged when bonus bets exist, and a separate Bonus
  profit number.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/hooks/useProfiles.ts frontend/src/components/ProfileSelector.tsx frontend/src/pages/BankrollPage.tsx frontend/src/pages/StatsPage.tsx
git commit -m "feat(ui): profile create form (purpose + sharp choice), labeled balances, bonus profit"
```

---

## Final verification

- [ ] `cd backend && pytest` — full suite green.
- [ ] `ruff check backend/src` — clean.
- [ ] `cd frontend && npm run lint` — clean.
- [ ] On a **copy** of the production DB: run the app once so `init_db` migration
  fires; confirm (a) per-profile SEK balance totals match pre-migration, (b) each
  sharp provider has exactly one `accounts` row, (c) no settled bet with a
  resolvable `(profile_id, provider_id)` has NULL `account_id`, (d) re-running is a
  no-op.
- [ ] Manual E2E of the three scenarios in Task 9 Step 5.
- [ ] **Do not deploy** — hand back to the user for the deploy decision (this is a
  schema migration on the live DB; take a DB dump first).

## Rollback

All work is on a branch — `git revert`/branch-delete restores code. The only
irreversible runtime step is the migration dropping `profile_provider_balances`;
mitigate by taking a Postgres dump before the first post-deploy `init_db` run. If
caution is preferred, comment out the `DROP TABLE profile_provider_balances` line
for the first release (the new reads use `accounts`; the old table simply lingers
unused as a fallback) and drop it in a follow-up once verified in production.

## Self-review notes (done)

- **Spec coverage:** profile dialog (T7/T9), shared labeled sharp (T1-T4),
  scoped fresh-sharp visibility via link table (T3/T7 tests assert isolation),
  Rule-B both-legs exclusion + separate bonus_profit (T8), migration off
  `ProfileProviderBalance` (T2), distinct-account totals (T3 `distinct_accounts`).
- **Type consistency:** `AccountRepo.resolve/get_or_create/link/set_balance/
  accounts_for_profile/distinct_accounts/link_count/has_bets`,
  `AccountService.provision/link_shared_sharp/create_fresh_sharp/create_soft/
  delete_profile_accounts`, `migrate_to_accounts(conn, unlimited_providers)` —
  names are used identically across tasks.
- **Open implementer checks flagged inline:** confirm `get_provider_currency`
  export name; confirm `copy_balances` has no other callers; locate the exact
  bet-create handler for the server-side `account_id` stamp.
