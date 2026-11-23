from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, database_url: str) -> None:
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=False,
        )

    def open(self) -> None:
        self.pool.open()
        self.pool.wait()

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def system_connection(self) -> Iterator[Connection]:
        with self.pool.connection() as connection, connection.transaction():
            yield connection

    @contextmanager
    def tenant_connection(self, tenant_id: UUID) -> Iterator[Connection]:
        with self.pool.connection() as connection, connection.transaction():
            connection.execute(
                "SELECT set_config('app.tenant_id', %s, true)",
                (str(tenant_id),),
            )
            yield connection

