import json
from pathlib import Path

def list_notes(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else []

def add(path, text):
    notes = list_notes(path)
    note = {"id": len(notes) + 1, "text": text.strip()}
    notes.append(note)
    Path(path).write_text(json.dumps(notes))
    return note
