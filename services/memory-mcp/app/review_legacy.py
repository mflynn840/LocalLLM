"""Operator-only review and adoption of memories created before workspace scoping."""
import argparse
import json
import sys
from uuid import UUID

from qdrant_client.http import models

from app.store import COLLECTION, PROJECT, _ensure_collection, client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adopt", nargs="+", type=UUID, help="Adopt explicitly reviewed IDs into this workspace")
    args = parser.parse_args()
    legacy = models.IsEmptyCondition(is_empty=models.PayloadField(key="project_id"))
    _ensure_collection()
    if args.adopt:
        ids = [str(memory_id) for memory_id in args.adopt]
        points = models.Filter(must=[legacy, models.HasIdCondition(has_id=ids)])
        eligible = client.count(collection_name=COLLECTION, count_filter=points, exact=True).count
        if eligible != len(set(ids)):
            sys.exit("At least one ID is missing or already scoped. Review the legacy list and retry.")
        client.set_payload(collection_name=COLLECTION, payload={"project_id": PROJECT, "kind": "decision"},
                           points=points, wait=True)
        print(f"Adopted {eligible} legacy records into the configured workspace.")
        return

    offset = None
    while True:
        rows, offset = client.scroll(collection_name=COLLECTION, scroll_filter=models.Filter(must=[legacy]),
                                     limit=100, offset=offset, with_payload=True)
        for row in rows:
            print(json.dumps({"id": str(row.id), **(row.payload or {})}, ensure_ascii=False))
        if offset is None:
            break


if __name__ == "__main__":
    main()
