class H:
    async def bad(self):
        rows = self.store.db_query("SELECT 1")
        return rows

    async def also_bad(self):
        time.sleep(0.5)
