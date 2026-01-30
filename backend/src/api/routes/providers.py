"""Provider API routes."""

from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from ...db.models import Provider
from ..deps import get_db
from ..schemas import ProviderCreate, ProviderUpdate

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("")
async def list_providers(db: Session = Depends(get_db)):
    """Get all providers with status and balance."""
    providers = db.query(Provider).all()
    return {
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "url": p.url,
                "is_enabled": p.is_enabled,
                "balance": p.balance,
            }
            for p in providers
        ],
        "total_balance": sum(p.balance for p in providers if p.is_enabled),
    }


@router.post("")
async def create_provider(provider: ProviderCreate, db: Session = Depends(get_db)):
    """Create a new provider."""
    existing = db.query(Provider).filter(Provider.id == provider.id).first()
    if existing:
        raise HTTPException(400, f"Provider {provider.id} already exists")

    p = Provider(
        id=provider.id,
        name=provider.name,
        url=provider.url,
        balance=provider.balance,
    )
    db.add(p)
    db.commit()
    return {"success": True, "provider_id": p.id}


@router.put("/{provider_id}")
async def update_provider(
    provider_id: str,
    data: ProviderUpdate,
    db: Session = Depends(get_db)
):
    """Update provider (balance, enabled, etc.)."""
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(404, f"Provider {provider_id} not found")

    old_balance = provider.balance

    if data.name is not None:
        provider.name = data.name
    if data.url is not None:
        provider.url = data.url
    if data.is_enabled is not None:
        provider.is_enabled = data.is_enabled
    if data.balance is not None:
        provider.balance = data.balance

    provider.updated_at = datetime.utcnow()
    db.commit()

    return {
        "success": True,
        "provider_id": provider_id,
        "old_balance": old_balance,
        "new_balance": provider.balance,
    }
