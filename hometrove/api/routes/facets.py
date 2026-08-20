"""Aggregated facet views over plugin results.

These power the tag / category / face landing pages. Each plugin writes
structured JSON in ``plugin_results.result_json``; this router reduces all
results into ``{name: count}`` maps the UI renders as chips / grids.

M0 reads every result row (libraries are small). An indexed side table or a
rolled-up materialized view is the M1 upgrade path.
"""

from __future__ import annotations

import json
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.models import PluginResult


router = APIRouter(prefix="/api", tags=["facets"])


def _counts_from_rows(rows, extractor) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for pr in rows:
        try:
            data = json.loads(pr.result_json or "{}")
        except json.JSONDecodeError:
            continue
        if pr.status != "ok":
            continue
        for value in extractor(data):
            if value:
                counter[value] += 1
    return dict(counter.most_common())


@router.get("/facets")
def facets(session: Session = Depends(get_db)):
    tags_rows = session.execute(
        select(PluginResult).where(PluginResult.plugin_id == "mock.tags")
    ).scalars().all()
    cat_rows = session.execute(
        select(PluginResult).where(PluginResult.plugin_id == "mock.category")
    ).scalars().all()

    tags = _counts_from_rows(tags_rows, lambda d: d.get("tags", []))
    categories = _counts_from_rows(
        cat_rows, lambda d: [d.get("category")] + ([d.get("subcategory")] if d.get("subcategory") else [])
    )

    # Persons are matched people, not detected names: count each person by
    # the total face count across their clusters, keyed by person id.
    from hometrove.models import FaceCluster, Person
    persons = session.execute(select(Person)).scalars().all()
    persons_map: dict[str, int] = {}
    for p in persons:
        total = sum(c.face_count for c in p.clusters)
        if total > 0:
            persons_map[str(p.id)] = total

    return {"tags": tags, "categories": categories, "persons": persons_map}
