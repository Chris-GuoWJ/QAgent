"""JSON conversation history, separate from experiment recovery state."""

import json
from uuid import uuid4

from langchain_core.messages import messages_from_dict, messages_to_dict

from .storage import Store


class ChatHistory:
    def __init__(self, store: Store, context_id: str, session_id: str | None = None):
        self.store = store
        self.session_id = session_id or uuid4().hex
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY, context_id TEXT NOT NULL, selected_run TEXT
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, message TEXT NOT NULL
                );
            """)
            row = db.execute("SELECT context_id FROM chat_sessions WHERE id=?", (self.session_id,)).fetchone()
            if row and row[0] != context_id:
                raise ValueError("会话的模型、后端或任务约定已变化，请使用原配置恢复或新建会话")
            if session_id and row is None:
                raise ValueError("找不到此对话会话；实验 run_id 与对话 session_id 是不同编号")
            db.execute("INSERT OR IGNORE INTO chat_sessions(id,context_id) VALUES(?,?)", (self.session_id, context_id))

    def append(self, message):
        value = messages_to_dict([message])[0]
        with self.store.connect() as db:
            db.execute("INSERT INTO chat_messages(session_id,message) VALUES(?,?)",
                       (self.session_id, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def messages(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT message FROM chat_messages WHERE session_id=? ORDER BY seq",
                              (self.session_id,)).fetchall()
        return messages_from_dict([json.loads(row[0]) for row in rows])
