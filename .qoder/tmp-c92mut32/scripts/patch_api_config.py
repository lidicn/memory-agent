"""Patch insights/api.py: wrap production Config with InsightConfig defaults."""
file_path = r"E:\NAS\memory-agent\src\memory_agent\insights\api.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

old = """    def __init__(self, store: Any = None, config: Optional[InsightConfig] = None,
                 repository: Optional[BaseRepository] = None) -> None:
        self.config = config or InsightConfig()
        self.repo: BaseRepository = repository or build_repository(store, self.config)"""

new = """    def __init__(self, store: Any = None, config: Optional[InsightConfig] = None,
                 repository: Optional[BaseRepository] = None) -> None:
        # 兼容生产 Config：非 InsightConfig 时用默认值包装，
        # 避免 'Config' object has no attribute 'cache_ttl'/'default_days'
        if config is None:
            self.config = InsightConfig()
        elif isinstance(config, InsightConfig):
            self.config = config
        else:
            self.config = InsightConfig()
            for attr in ("tz_offset_hours", "default_days", "default_limit"):
                val = getattr(config, attr, None)
                if val is not None:
                    try:
                        setattr(self.config, attr, val)
                    except Exception:
                        pass
        self.repo: BaseRepository = repository or build_repository(store, self.config)"""

if old in content:
    content = content.replace(old, new)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("OK: api.py config wrapping added")
else:
    print("ERROR: old string not found")
