class H:
    async def good(self):
        rows = await asyncio.to_thread(self.store.db_query, "SELECT 1")
        return rows
