"""Add db_query method to Store class for new insights framework compatibility."""
file_path = r"E:\NAS\memory-agent\src\memory_agent\store.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

old = """    @contextmanager
    def _db(self):
        \"\"\"P1-3: Acquire lock and yield connection, protecting execute/commit.\"\"\"
        with self._lock:
            yield self.connect()

    def check_and_recover"""

new = """    @contextmanager
    def _db(self):
        \"\"\"P1-3: Acquire lock and yield connection, protecting execute/commit.\"\"\"
        with self._lock:
            yield self.connect()

    def db_query(self, sql: str, params: tuple = ()) -> list:
        \"\"\"Raw SQL query (read-only) for new insights framework compatibility.

        Returns rows as list of dicts. Used by insights.StoreRepository.
        \"\"\"
        with self._db() as conn:
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def check_and_recover"""

if old in content:
    content = content.replace(old, new)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("OK: db_query method added to Store")
else:
    print("ERROR: old string not found")
