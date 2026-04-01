"""One-time script: ban Coolbet and Snabbare for active profile."""

import sys
sys.path.insert(0, ".")

from src.db.models import get_session, Profile
from src.services.limit_service import LimitService


def main():
    session = get_session()
    profile = session.query(Profile).filter(Profile.is_active == True).first()
    if not profile:
        print("No active profile found")
        return

    service = LimitService(session)
    for provider_id, notes in [
        ("coolbet", "Account closed — 'Ditt konto är stängt' dialog on login"),
        ("snabbare", "Account closed — blocked from site"),
    ]:
        result = service.ban_provider(
            profile_id=profile.id,
            provider_id=provider_id,
            notes=notes,
        )
        status = "OK" if result["success"] else result.get("error", "unknown error")
        print(f"  {provider_id}: {status}")

    session.close()


if __name__ == "__main__":
    main()
