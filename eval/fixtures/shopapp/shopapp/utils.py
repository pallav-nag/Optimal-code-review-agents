"""Small helpers shared by handlers."""


def paginate(items: list, page: int, size: int = 20) -> list:
    if page < 1 or size < 1:
        raise ValueError("page and size must be positive")
    start = (page - 1) * size
    return items[start : start + size]


def chunked(items: list, n: int) -> list[list]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
