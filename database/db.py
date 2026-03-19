import aiosqlite
import os

DB_PATH = "intrabot.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_user_id INTEGER PRIMARY KEY,
                is_login TEXT,
                is_password_b64 TEXT,
                is_user_id INTEGER,
                last_task_id INTEGER DEFAULT 0,
                last_comment_id INTEGER DEFAULT 0,
                last_check_time TEXT
            )
        """)
        await db.commit()

async def add_or_update_user(tg_id, login, password_b64, is_user_id=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (tg_user_id, is_login, is_password_b64, is_user_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tg_user_id) DO UPDATE SET
                is_login = excluded.is_login,
                is_password_b64 = excluded.is_password_b64,
                is_user_id = COALESCE(excluded.is_user_id, users.is_user_id)
        """, (tg_id, login, password_b64, is_user_id))
        await db.commit()

async def get_user(tg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE tg_user_id = ?", (tg_id,)) as cursor:
            return await cursor.fetchone()

async def get_all_users():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cursor:
            return await cursor.fetchall()

async def update_user_state(tg_id, last_task_id=None, last_comment_id=None, last_check_time=None):
    async with aiosqlite.connect(DB_PATH) as db:
        fields = []
        values = []
        
        if last_task_id is not None:
            fields.append("last_task_id = ?")
            values.append(last_task_id)
        if last_comment_id is not None:
            fields.append("last_comment_id = ?")
            values.append(last_comment_id)
        if last_check_time is not None:
            fields.append("last_check_time = ?")
            values.append(last_check_time)
            
        if fields:
            query = f"UPDATE users SET {', '.join(fields)} WHERE tg_user_id = ?"
            values.append(tg_id)
            await db.execute(query, tuple(values))
            await db.commit()
