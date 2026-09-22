"""Contacts (admin auth)"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import schema
from ..auth import require_admin_user

router = APIRouter(prefix="/api/contacts", tags=["contacts"], dependencies=[Depends(require_admin_user)])


def _row_out(r: dict) -> dict:
    return {
        "id": r["id"],
        "name": r["name"],
        "phone": r["phone"],
        "note": r.get("note") or "",
        "created_at": r.get("created_at"),
    }


def _duplicate(err) -> bool:
    return isinstance(err, sqlite3.IntegrityError) and "UNIQUE" in str(err)


@router.get("")
def list_contacts(request: Request):
    db = request.app.state.db
    return [_row_out(r) for r in db.rows("SELECT * FROM contacts ORDER BY name, id")]


@router.post("", status_code=201)
def create_contact(body: schema.ContactIn, request: Request):
    name, phone, note = body.name.strip(), body.phone.strip(), (body.note or "").strip()
    if not name:
        raise HTTPException(422, "Name cannot be empty")
    if not phone:
        raise HTTPException(422, "Phone number cannot be empty")
    db = request.app.state.db
    try:
        mid = db.execute(
            "INSERT INTO contacts (name, phone, note) VALUES (?,?,?)",
            (name, phone, note),
        )
    except sqlite3.IntegrityError as exc:
        if _duplicate(exc):
            raise HTTPException(409, "A contact with this number already exists")
        raise
    return _row_out(db.row("SELECT * FROM contacts WHERE id=?", (mid,)))


@router.put("/{contact_id}")
def update_contact(contact_id: int, body: schema.ContactIn, request: Request):
    name, phone, note = body.name.strip(), body.phone.strip(), (body.note or "").strip()
    if not name:
        raise HTTPException(422, "Name cannot be empty")
    if not phone:
        raise HTTPException(422, "Phone number cannot be empty")
    db = request.app.state.db
    if not db.row("SELECT id FROM contacts WHERE id=?", (contact_id,)):
        raise HTTPException(404, "Contact not found")
    try:
        db.execute(
            "UPDATE contacts SET name=?, phone=?, note=? WHERE id=?",
            (name, phone, note, contact_id),
        )
    except sqlite3.IntegrityError as exc:
        if _duplicate(exc):
            raise HTTPException(409, "A contact with this number already exists")
        raise
    return _row_out(db.row("SELECT * FROM contacts WHERE id=?", (contact_id,)))


@router.delete("/{contact_id}")
def delete_contact(contact_id: int, request: Request):
    db = request.app.state.db
    if not db.row("SELECT id FROM contacts WHERE id=?", (contact_id,)):
        raise HTTPException(404, "Contact not found")
    db.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    return {"ok": True}