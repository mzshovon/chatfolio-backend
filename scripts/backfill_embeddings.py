"""Enqueue embed_content_job for every Experience/Project/Skill/Education row that doesn't yet
have a VectorEmbedding pointer.

Needed once: the api/worker containers ran without an editable install until 2026-08-21 (see
docs/BACKEND_PLAN.md §16), so every embedding-trigger call made through them before that fix
silently landed on stale code and never actually enqueued anything. Candidates who filled in
their profile before the fix have data that's correctly stored in Postgres but was never
embedded, so recruiter chat can't ground answers on it — this backfills those rows.

Safe to re-run anytime: embed_content_job upserts by (source_type, source_id), so re-embedding
an already-embedded row just overwrites it with identical content, and rows without a
`--force` are skipped if a pointer already exists.

Usage (from inside the api or worker container, which already has the app and its
dependencies installed):
    docker compose exec api python scripts/backfill_embeddings.py
    docker compose exec api python scripts/backfill_embeddings.py --profile-id <uuid>
    docker compose exec api python scripts/backfill_embeddings.py --force  # re-embed everything
"""

import argparse
import asyncio
import uuid

from sqlalchemy import select

from chatfolio.db.session import get_sessionmaker
from chatfolio.models.embedding import VectorEmbedding
from chatfolio.models.profile import CandidateProfile
from chatfolio.services.embedding_service import EMBEDDABLE_CHILD_TYPES
from chatfolio.workers.queue import get_arq_pool


async def backfill(profile_id: uuid.UUID | None, force: bool) -> None:
    sessionmaker = get_sessionmaker()
    pool = await get_arq_pool()
    enqueued = 0
    skipped = 0

    async with sessionmaker() as session:
        if profile_id is not None:
            profile_ids = [profile_id]
        else:
            profile_result = await session.execute(select(CandidateProfile.id))
            profile_ids = list(profile_result.scalars().all())

        for pid in profile_ids:
            for source_type, (model, chunk_builder) in EMBEDDABLE_CHILD_TYPES.items():
                child_result = await session.execute(select(model).where(model.profile_id == pid))
                for row in child_result.scalars().all():
                    if not force:
                        chroma_ref_id = f"{source_type}:{row.id}"
                        pointer_result = await session.execute(
                            select(VectorEmbedding.id).where(
                                VectorEmbedding.chroma_ref_id == chroma_ref_id
                            )
                        )
                        if pointer_result.scalar_one_or_none() is not None:
                            skipped += 1
                            continue

                    text = chunk_builder(row)
                    if not text.strip():
                        continue
                    await pool.enqueue_job(
                        "embed_content_job", str(pid), source_type, str(row.id), text
                    )
                    enqueued += 1

    print(f"Enqueued {enqueued} embedding jobs, skipped {skipped} already-embedded rows.")
    print("The worker container will process these shortly — check `docker compose logs worker`.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-id", type=uuid.UUID, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    asyncio.run(backfill(args.profile_id, args.force))


if __name__ == "__main__":
    main()
