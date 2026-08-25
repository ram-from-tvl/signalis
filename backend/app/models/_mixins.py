"""Shared helpers for default column values across ORM models."""
from __future__ import annotations

import datetime
import uuid


def new_uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()
