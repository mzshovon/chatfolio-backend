from typing import Protocol, TypedDict


class QueryMatch(TypedDict):
    id: str
    document: str
    distance: float
    metadata: dict[str, str]


class VectorStore(Protocol):
    async def upsert(
        self,
        *,
        collection: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, str]],
    ) -> None: ...

    async def delete(self, *, collection: str, ids: list[str]) -> None: ...

    async def query(
        self,
        *,
        collection: str,
        query_embedding: list[float],
        n_results: int = 5,
        where: dict[str, str] | None = None,
    ) -> list[QueryMatch]: ...
