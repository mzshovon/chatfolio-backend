class FakeVectorStore:
    def __init__(self) -> None:
        self.upserted: list[dict[str, object]] = []
        self.deleted_ids: list[str] = []

    async def upsert(
        self,
        *,
        collection: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, str]],
    ) -> None:
        self.upserted.append(
            {"collection": collection, "ids": ids, "documents": documents, "metadatas": metadatas}
        )

    async def delete(self, *, collection: str, ids: list[str]) -> None:
        self.deleted_ids.extend(ids)

    async def query(
        self,
        *,
        collection: str,
        query_embedding: list[float],
        n_results: int = 5,
        where: dict[str, str] | None = None,
    ) -> list[dict[str, object]]:
        return []
