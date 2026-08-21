from chatfolio.vectorstore.base import QueryMatch


class FakeVectorStore:
    def __init__(self, query_results: list[QueryMatch] | None = None) -> None:
        self.upserted: list[dict[str, object]] = []
        self.deleted_ids: list[str] = []
        self.query_results = query_results or []

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
    ) -> list[QueryMatch]:
        return self.query_results[:n_results]
