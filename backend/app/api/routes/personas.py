from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.entities import Persona
from app.schemas.schemas import PersonaCreate, PersonaOut

router = APIRouter(prefix="/api/personas", tags=["personas"])


@router.get("", response_model=list[PersonaOut])
def list_personas(db: Session = Depends(get_db)):
    return db.execute(select(Persona).order_by(Persona.created_at.desc())).scalars().all()


@router.post("", response_model=PersonaOut, status_code=201)
def create_persona(payload: PersonaCreate, db: Session = Depends(get_db)):
    persona = Persona(**payload.model_dump())
    db.add(persona)
    db.commit()
    db.refresh(persona)
    return persona


@router.get("/{persona_id}", response_model=PersonaOut)
def get_persona(persona_id: str, db: Session = Depends(get_db)):
    persona = db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    return persona


@router.put("/{persona_id}", response_model=PersonaOut)
def update_persona(persona_id: str, payload: PersonaCreate, db: Session = Depends(get_db)):
    persona = db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    for key, value in payload.model_dump().items():
        setattr(persona, key, value)
    db.add(persona)
    db.commit()
    db.refresh(persona)
    return persona


@router.delete("/{persona_id}", status_code=204)
def delete_persona(persona_id: str, db: Session = Depends(get_db)):
    persona = db.get(Persona, persona_id)
    if not persona:
        raise HTTPException(404, "Persona not found")
    db.delete(persona)
    db.commit()
