"""SQLite persistence for products, private channels, and farm entries."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Product:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class FarmChannel:
    guild_id: int
    member_id: int
    channel_id: int
    panel_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class Goal:
    id: int
    guild_id: int
    start_at: str
    end_at: str
    status: str


@dataclass(frozen=True, slots=True)
class GoalProgress:
    product: Product
    target: Decimal
    current: Decimal


class Database:
    """Small repository that opens one short-lived connection per operation."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL COLLATE NOCASE,
                    created_at TEXT NOT NULL,
                    UNIQUE (guild_id, name)
                );
                CREATE TABLE IF NOT EXISTS farm_channels (
                    guild_id INTEGER NOT NULL,
                    member_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, member_id)
                );
                CREATE TABLE IF NOT EXISTS farm_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    member_id INTEGER NOT NULL,
                    actor_id INTEGER NOT NULL,
                    actor_was_admin INTEGER NOT NULL,
                    product_id INTEGER,
                    product_name TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_entries_member
                ON farm_entries (guild_id, member_id, created_at);
                CREATE TABLE IF NOT EXISTS stock_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    actor_id INTEGER NOT NULL,
                    product_id INTEGER,
                    product_name TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
                );
                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'closed')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS goal_items (
                    goal_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    product_name TEXT NOT NULL,
                    target TEXT NOT NULL,
                    PRIMARY KEY (goal_id, product_id),
                    FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS admin_panels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, message_id)
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(farm_channels)").fetchall()
            }
            if "panel_message_id" not in columns:
                connection.execute("ALTER TABLE farm_channels ADD COLUMN panel_message_id INTEGER")

    def list_products(self, guild_id: int) -> list[Product]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name FROM products WHERE guild_id = ? ORDER BY name",
                (guild_id,),
            ).fetchall()
        return [Product(id=row["id"], name=row["name"]) for row in rows]

    def add_product(self, guild_id: int, name: str) -> Product:
        clean_name = " ".join(name.split())
        if not clean_name or len(clean_name) > 100:
            raise ValueError("O nome do produto deve ter entre 1 e 100 caracteres")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO products (guild_id, name, created_at) VALUES (?, ?, ?)",
                (guild_id, clean_name, datetime.now(UTC).isoformat()),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite não retornou o identificador do produto")
            product_id = cursor.lastrowid
        return Product(product_id, clean_name)

    def remove_product(self, guild_id: int, product_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM products WHERE guild_id = ? AND id = ?",
                (guild_id, product_id),
            )
        return cursor.rowcount > 0

    def get_farm_channel(self, guild_id: int, member_id: int) -> FarmChannel | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT guild_id, member_id, channel_id, panel_message_id FROM farm_channels
                WHERE guild_id = ? AND member_id = ?""",
                (guild_id, member_id),
            ).fetchone()
        return FarmChannel(**dict(row)) if row else None

    def get_farm_channel_by_channel(self, channel_id: int) -> FarmChannel | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT guild_id, member_id, channel_id, panel_message_id FROM farm_channels
                WHERE channel_id = ?""",
                (channel_id,),
            ).fetchone()
        return FarmChannel(**dict(row)) if row else None

    def list_farm_channels(self, guild_id: int) -> list[FarmChannel]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT guild_id, member_id, channel_id, panel_message_id
                FROM farm_channels WHERE guild_id = ? ORDER BY member_id""",
                (guild_id,),
            ).fetchall()
        return [FarmChannel(**dict(row)) for row in rows]

    def save_farm_channel(
        self,
        guild_id: int,
        member_id: int,
        channel_id: int,
        panel_message_id: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO farm_channels (
                    guild_id, member_id, channel_id, panel_message_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (guild_id, member_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    panel_message_id = COALESCE(excluded.panel_message_id, panel_message_id)""",
                (
                    guild_id,
                    member_id,
                    channel_id,
                    panel_message_id,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def add_entry(
        self,
        *,
        guild_id: int,
        member_id: int,
        actor_id: int,
        actor_was_admin: bool,
        product: Product,
        quantity: Decimal,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO farm_entries (
                    guild_id, member_id, actor_id, actor_was_admin,
                    product_id, product_name, quantity, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    guild_id,
                    member_id,
                    actor_id,
                    int(actor_was_admin),
                    product.id,
                    product.name,
                    format(quantity, "f"),
                    datetime.now(UTC).isoformat(),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite não retornou o identificador do registro")
            return cursor.lastrowid

    def product_totals(
        self,
        guild_id: int,
        *,
        member_id: int | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
    ) -> dict[str, Decimal]:
        clauses = ["guild_id = ?"]
        parameters: list[int | str] = [guild_id]
        if member_id is not None:
            clauses.append("member_id = ?")
            parameters.append(member_id)
        if start_at is not None:
            clauses.append("created_at >= ?")
            parameters.append(start_at)
        if end_at is not None:
            clauses.append("created_at <= ?")
            parameters.append(end_at)
        query = f"SELECT product_name, quantity FROM farm_entries WHERE {' AND '.join(clauses)}"
        totals: dict[str, Decimal] = {}
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        for row in rows:
            totals[row["product_name"]] = totals.get(row["product_name"], Decimal(0)) + Decimal(
                row["quantity"]
            )
        return totals

    def stock_totals(self, guild_id: int) -> dict[str, Decimal]:
        totals = self.product_totals(guild_id)
        with self._connect() as connection:
            outputs = connection.execute(
                "SELECT product_name, quantity FROM stock_outputs WHERE guild_id = ?",
                (guild_id,),
            ).fetchall()
        for row in outputs:
            totals[row["product_name"]] = totals.get(row["product_name"], Decimal(0)) - Decimal(
                row["quantity"]
            )
        return totals

    def add_output(
        self, *, guild_id: int, actor_id: int, product: Product, quantity: Decimal
    ) -> int:
        if quantity <= 0:
            raise ValueError("A quantidade deve ser maior que zero")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            entry_rows = connection.execute(
                """SELECT quantity FROM farm_entries
                WHERE guild_id = ? AND product_name = ?""",
                (guild_id, product.name),
            ).fetchall()
            output_rows = connection.execute(
                """SELECT quantity FROM stock_outputs
                WHERE guild_id = ? AND product_name = ?""",
                (guild_id, product.name),
            ).fetchall()
            available = sum((Decimal(row["quantity"]) for row in entry_rows), Decimal(0)) - sum(
                (Decimal(row["quantity"]) for row in output_rows), Decimal(0)
            )
            if quantity > available:
                raise ValueError(f"Estoque insuficiente. Disponível: {format(available, 'f')}")
            cursor = connection.execute(
                """INSERT INTO stock_outputs (
                    guild_id, actor_id, product_id, product_name, quantity, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    guild_id,
                    actor_id,
                    product.id,
                    product.name,
                    format(quantity, "f"),
                    datetime.now(UTC).isoformat(),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite não retornou o identificador da saída")
            return cursor.lastrowid

    def create_goal(self, guild_id: int, start_at: str, end_at: str) -> Goal:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO goals (guild_id, start_at, end_at, status, created_at)
                VALUES (?, ?, ?, 'draft', ?)""",
                (guild_id, start_at, end_at, datetime.now(UTC).isoformat()),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite não retornou o identificador da meta")
            goal_id = cursor.lastrowid
        return Goal(goal_id, guild_id, start_at, end_at, "draft")

    def set_goal_item(self, goal_id: int, product: Product, target: Decimal) -> None:
        if target <= 0:
            raise ValueError("A meta deve ser maior que zero")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO goal_items (goal_id, product_id, product_name, target)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (goal_id, product_id) DO UPDATE SET target = excluded.target""",
                (goal_id, product.id, product.name, format(target, "f")),
            )

    def activate_goal(self, guild_id: int, goal_id: int) -> None:
        with self._connect() as connection:
            item_count = connection.execute(
                "SELECT COUNT(*) FROM goal_items WHERE goal_id = ?", (goal_id,)
            ).fetchone()[0]
            if item_count == 0:
                raise ValueError("Adicione pelo menos um produto à meta")
            connection.execute(
                "UPDATE goals SET status = 'closed' WHERE guild_id = ? AND status = 'active'",
                (guild_id,),
            )
            connection.execute(
                "UPDATE goals SET status = 'active' WHERE guild_id = ? AND id = ?",
                (guild_id, goal_id),
            )

    def close_active_goal(self, guild_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE goals SET status = 'closed' WHERE guild_id = ? AND status = 'active'",
                (guild_id,),
            )
        return cursor.rowcount > 0

    def get_active_goal(self, guild_id: int) -> Goal | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id, guild_id, start_at, end_at, status FROM goals
                WHERE guild_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1""",
                (guild_id,),
            ).fetchone()
        return Goal(**dict(row)) if row else None

    def goal_progress(self, guild_id: int, member_id: int) -> list[GoalProgress]:
        goal = self.get_active_goal(guild_id)
        if goal is None:
            return []
        totals = self.product_totals(
            guild_id,
            member_id=member_id,
            start_at=goal.start_at,
            end_at=goal.end_at,
        )
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT product_id, product_name, target FROM goal_items
                WHERE goal_id = ? ORDER BY product_name""",
                (goal.id,),
            ).fetchall()
        return [
            GoalProgress(
                Product(row["product_id"], row["product_name"]),
                Decimal(row["target"]),
                totals.get(row["product_name"], Decimal(0)),
            )
            for row in rows
        ]

    def save_admin_panel(self, guild_id: int, channel_id: int, message_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO admin_panels (guild_id, channel_id, message_id)
                VALUES (?, ?, ?)""",
                (guild_id, channel_id, message_id),
            )

    def list_admin_panels(self, guild_id: int) -> list[tuple[int, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT channel_id, message_id FROM admin_panels WHERE guild_id = ?",
                (guild_id,),
            ).fetchall()
        return [(row["channel_id"], row["message_id"]) for row in rows]
