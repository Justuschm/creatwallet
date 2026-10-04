"""Rate-Limits je Minute, gezählt in der Datenbank - gilt damit über alle API-Prozesse und Server."""

import time

from sqlalchemy import select

from .models import RateCounter


def hit(session, bucket, limit):
    """Zählt eine Anfrage. Rückgabe (erlaubt, Sekunden bis zum nächsten Fenster)."""
    if not limit:
        return True, 0
    now = time.time()
    window = int(now // 60)
    values = {"bucket": bucket[:120], "window": window, "count": 1}
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(RateCounter).values(**values)
    stmt = stmt.on_conflict_do_update(index_elements=["bucket", "window"],
                                      set_={"count": RateCounter.count + 1})
    session.execute(stmt)
    count = session.scalar(select(RateCounter.count).where(RateCounter.bucket == values["bucket"],
                                                           RateCounter.window == window))
    session.commit()
    return count <= limit, int(60 - now % 60) + 1
