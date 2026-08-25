from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.entities import Solution
from app.schemas.schemas import SolutionCreate, SolutionOut

router = APIRouter(prefix="/api/solutions", tags=["solutions"])


@router.get("", response_model=list[SolutionOut])
def list_solutions(db: Session = Depends(get_db)):
    return db.execute(select(Solution).order_by(Solution.created_at.desc())).scalars().all()


@router.post("", response_model=SolutionOut, status_code=201)
def create_solution(payload: SolutionCreate, db: Session = Depends(get_db)):
    solution = Solution(**payload.model_dump())
    db.add(solution)
    db.commit()
    db.refresh(solution)
    return solution


@router.get("/{solution_id}", response_model=SolutionOut)
def get_solution(solution_id: str, db: Session = Depends(get_db)):
    solution = db.get(Solution, solution_id)
    if not solution:
        raise HTTPException(404, "Solution not found")
    return solution


@router.put("/{solution_id}", response_model=SolutionOut)
def update_solution(solution_id: str, payload: SolutionCreate, db: Session = Depends(get_db)):
    solution = db.get(Solution, solution_id)
    if not solution:
        raise HTTPException(404, "Solution not found")
    for key, value in payload.model_dump().items():
        setattr(solution, key, value)
    db.add(solution)
    db.commit()
    db.refresh(solution)
    return solution


@router.delete("/{solution_id}", status_code=204)
def delete_solution(solution_id: str, db: Session = Depends(get_db)):
    solution = db.get(Solution, solution_id)
    if not solution:
        raise HTTPException(404, "Solution not found")
    db.delete(solution)
    db.commit()
