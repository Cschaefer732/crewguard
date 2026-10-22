"""Small text helpers shared across the CLI and cogs."""


def chunk_message(text: str, limit: int = 2000) -> list[str]:
    """Split text into <=limit pieces on line boundaries — Discord rejects messages over 2000
    chars. A single over-long line is hard-split. Lossless: "".join(result) == text."""
    out: list[str] = []
    cur = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # a single very long line — hard-split it
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            out.append(cur)
            cur = ""
        cur += line
    if cur:
        out.append(cur)
    return out
