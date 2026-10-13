"""Explicit, conversation-scoped activation; independent of goals and directories."""

from __future__ import annotations

import json

from .contract import identifier
from .storage import LoopError, now


class Harness:
    def __init__(self, knowledge):
        self.knowledge = knowledge

    def status(self, session_id):
        if not identifier(session_id):
            raise LoopError("Invalid session ID")
        session = self.knowledge.get("session", session_id)
        activation = session.get("harness", {})
        return {"session_id": session_id, "provider": session["provider"],
                "enabled": activation.get("enabled", False),
                "scope": "conversation", "inputs": "all",
                "activated_at": activation.get("activated_at"),
                "updated_at": activation.get("updated_at"),
                "knowledge_home": str(self.knowledge.home)}

    def on(self, provider, session_id=None):
        # Without a bound ID this is a new conversation, never the latest session.
        session = self.knowledge.session(provider, session_id)
        return self.set_enabled(session["id"], True)

    def off(self, session_id):
        return self.set_enabled(session_id, False)

    def set_enabled(self, session_id, enabled):
        if not identifier(session_id):
            raise LoopError("Invalid session ID")
        with self.knowledge.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM records WHERE kind='session' AND id=?", (session_id,)).fetchone()
            if row is None:
                raise LoopError(f"Unknown session: {session_id}")
            session = json.loads(row[0])
            activation = session.get("harness", {})
            if activation.get("enabled", False) != enabled:
                stamp = now()
                session["harness"] = {**activation, "enabled": enabled, "updated_at": stamp}
                if enabled:
                    session["harness"]["activated_at"] = stamp
                self.knowledge.put("session", session, db)
        # No goal, retry budget, source or knowledge topic is changed by a toggle.
        return self.status(session_id)
