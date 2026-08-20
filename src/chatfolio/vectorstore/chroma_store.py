from typing import Any

import chromadb

from chatfolio.config.settings import VectorStoreSettings
from chatfolio.vectorstore.base import QueryMatch


class ChromaVectorStore:
    """Embeddings are always passed in explicitly (never computed server-side) — see
    vectorstore/local_embedder.py — so this adapter has no embedding-function configuration
    of its own and works against any Chroma server version without extra setup.
    """

    def __init__(self, settings: VectorStoreSettings) -> None:
        self._settings = settings
        self._client: Any | None = None

    async def _get_client(self) -> Any:
        if self._client is None:
            self._client = await chromadb.AsyncHttpClient(
                host=self._settings.host, port=self._settings.port
            )
        return self._client

    async def _get_collection(self, name: str) -> Any:
        client = await self._get_client()
        return await client.get_or_create_collection(name=name)

    async def upsert(
        self,
        *,
        collection: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, str]],
    ) -> None:
        coll = await self._get_collection(collection)
        await coll.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

    async def delete(self, *, collection: str, ids: list[str]) -> None:
        coll = await self._get_collection(collection)
        await coll.delete(ids=ids)

    async def query(
        self,
        *,
        collection: str,
        query_embedding: list[float],
        n_results: int = 5,
        where: dict[str, str] | None = None,
    ) -> list[QueryMatch]:
        coll = await self._get_collection(collection)
        result = await coll.query(
            query_embeddings=[query_embedding], n_results=n_results, where=where
        )

        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        distances = result.get("distances", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]

        return [
            QueryMatch(
                id=ids[i], document=documents[i], distance=distances[i], metadata=metadatas[i]
            )
            for i in range(len(ids))
        ]
