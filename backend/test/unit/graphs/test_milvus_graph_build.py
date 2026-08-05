from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from yuxi.knowledge.graphs.extractors import (
    GraphExtractorFactory,
    LLMGraphExtractor,
    normalize_extraction_result,
)
from yuxi.knowledge.graphs.extractors import llm as llm_extractor_module
from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService


def _raw_graph_node(node_id: str, *, labels: list[str] | None = None, name: str | None = None) -> dict:
    return {
        "id": node_id,
        "labels": labels or ["MilvusKB", "Entity"],
        "properties": {"name": name or node_id, "kb_id": "kb_test"},
    }


def _raw_graph_edge(edge_id: str, source_id: str, target_id: str) -> dict:
    return {
        "id": edge_id,
        "type": "RELATED_TO",
        "source_id": source_id,
        "target_id": target_id,
        "properties": {},
    }


def test_normalize_extraction_result_defaults_and_validates_refs():
    result = normalize_extraction_result(
        {
            "entities": [{"text": "张三"}, {"text": "公司"}],
            "relations": [{"source": "张三", "target": "公司", "text": "任职于"}],
        },
        "llm",
    )

    assert result["entities"][0]["label"] == "Entity"
    assert result["relations"][0]["label"] == "RELATED_TO"
    assert result["relations"][0]["source"] == {"text": "张三", "label": "Entity", "attributes": []}
    assert result["metadata"] == {"extractor_type": "llm", "schema_version": 1}


def test_normalize_extraction_result_accepts_llm_nested_relation_entities():
    result = normalize_extraction_result(
        {
            "relations": [
                {
                    "source": {
                        "text": "张三",
                        "label": "Person",
                        "attributes": [{"text": "工程师", "label": "Occupation"}],
                    },
                    "target": {"text": "公司", "label": "Organization"},
                    "text": "任职于",
                    "label": "WORKS_AT",
                }
            ]
        },
        "llm",
    )

    assert result["entities"] == [
        {"text": "张三", "label": "Person", "attributes": [{"text": "工程师", "label": "Occupation"}]},
        {"text": "公司", "label": "Organization", "attributes": []},
    ]
    assert result["relations"][0]["source"]["attributes"] == [{"text": "工程师", "label": "Occupation"}]
    assert result["relations"][0]["target"] == {"text": "公司", "label": "Organization", "attributes": []}


@pytest.mark.parametrize(
    "payload",
    [
        {"entities": [{"text": "张三"}], "relations": [{"source": "张三", "target": "不存在", "text": "关系"}]},
        {"entities": [{"text": ""}], "relations": []},
    ],
)
def test_normalize_extraction_result_rejects_invalid_payload(payload):
    with pytest.raises(ValueError):
        normalize_extraction_result(payload, "llm")


def test_llm_graph_extractor_rejects_custom_prompt():
    extractor = LLMGraphExtractor({"model_spec": "test/model", "prompt": "custom"})

    with pytest.raises(ValueError, match="不支持自定义完整 Prompt"):
        extractor.validate_options()


def test_llm_graph_extractor_appends_schema_to_fixed_prompt():
    extractor = LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "schema": "实体类型只能是 Person 或 Organization",
            "concurrency_count": 5,
            "model_params": {"temperature": 0.1},
        }
    )

    prompt = extractor._build_prompt("张三任职于公司")

    assert "请从下面文本中抽取实体和实体关系" in prompt
    assert "抽取 Schema 约束" in prompt
    assert "实体类型只能是 Person 或 Organization" in prompt
    assert "文本：\n张三任职于公司" in prompt


@pytest.mark.asyncio
async def test_llm_graph_extractor_retries_timeout_then_succeeds(monkeypatch):
    model = SimpleNamespace(
        call=AsyncMock(
            side_effect=[
                Exception("Error calling model: Request timed out."),
                SimpleNamespace(content='{"relations": []}'),
            ]
        )
    )
    select_model = MagicMock(return_value=model)
    sleep = AsyncMock()
    monkeypatch.setattr(llm_extractor_module, "select_model", select_model)
    monkeypatch.setattr(llm_extractor_module.asyncio, "sleep", sleep)
    extractor = LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "request_timeout_seconds": 180,
            "timeout_retries": 1,
        }
    )

    result = await extractor.extract("张三任职于公司", chunk_metadata={"chunk_id": "chunk_1"})

    assert result == {"relations": []}
    assert model.call.await_count == 2
    select_model.assert_called_once_with(model_spec="test/model", timeout=180.0, model_params={})
    sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_llm_graph_extractor_does_not_retry_non_timeout_error(monkeypatch):
    model = SimpleNamespace(call=AsyncMock(side_effect=ValueError("invalid response")))
    monkeypatch.setattr(llm_extractor_module, "select_model", MagicMock(return_value=model))
    sleep = AsyncMock()
    monkeypatch.setattr(llm_extractor_module.asyncio, "sleep", sleep)
    extractor = LLMGraphExtractor({"model_spec": "test/model", "timeout_retries": 1})

    with pytest.raises(ValueError, match="invalid response"):
        await extractor.extract("张三任职于公司")

    assert model.call.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"request_timeout_seconds": 20}, "30 到 600"),
        ({"request_timeout_seconds": 601}, "30 到 600"),
        ({"timeout_retries": -1}, "0 到 3"),
        ({"timeout_retries": 4}, "0 到 3"),
    ],
)
def test_llm_graph_extractor_validates_timeout_options(options, message):
    extractor = LLMGraphExtractor({"model_spec": "test/model", **options})

    with pytest.raises(ValueError, match=message):
        extractor.validate_options()


def test_graph_extractor_factory_supports_only_llm():
    assert GraphExtractorFactory.supported_types() == ["llm"]


def test_graph_extractor_factory_rejects_spacy():
    with pytest.raises(ValueError, match="spacy"):
        GraphExtractorFactory.create("spacy", {"model": "zh_core_web_sm"})


@pytest.mark.asyncio
async def test_milvus_graph_service_configure_rejects_spacy():
    kb = SimpleNamespace(kb_type="milvus", additional_params={})

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

        async def update(self, kb_id, data):
            raise AssertionError("unsupported extractor should not be persisted")

    service = MilvusGraphService(kb_repo=Repo())

    with pytest.raises(ValueError, match="不支持的图谱抽取器类型"):
        await service.configure(
            "kb_test",
            extractor_type="spacy",
            extractor_options={"model": "zh_core_web_sm"},
            created_by="user_1",
        )


@pytest.mark.asyncio
async def test_milvus_graph_service_configure_persists_updated_concurrency():
    kb = SimpleNamespace(
        kb_type="milvus",
        additional_params={
            "graph_build_config": {
                "locked": True,
                "extractor_type": "llm",
                "extractor_options": {"model_spec": "test/model", "concurrency_count": 5},
            }
        },
    )

    class Repo:
        async def get_by_kb_id(self, kb_id):
            return kb

        async def update(self, kb_id, data):
            kb.additional_params = data["additional_params"]
            return kb

    chunk_repo = SimpleNamespace(
        count_by_kb_id=AsyncMock(return_value=0),
        count_graph_pending_by_kb_id=AsyncMock(return_value=0),
        count_graph_indexed_by_kb_id=AsyncMock(return_value=0),
    )
    graph_repo = SimpleNamespace(count_by_kb_id=AsyncMock(return_value=(3, 2)))
    service = MilvusGraphService(kb_repo=Repo(), chunk_repo=chunk_repo, graph_repo=graph_repo)

    await service.configure(
        "kb_test",
        extractor_type="llm",
        extractor_options={"model_spec": "test/model", "concurrency_count": 9},
        created_by="user_1",
    )
    status = await service.get_status("kb_test")

    assert status["config"]["extractor_options"]["concurrency_count"] == 9
    assert status["entity_count"] == 3
    assert status["relationship_count"] == 2


@pytest.mark.asyncio
async def test_graph_status_ignores_historical_failure_after_all_chunks_are_indexed():
    kb = SimpleNamespace(kb_type="milvus", additional_params={"graph_build_config": {"locked": True}})
    kb_repo = SimpleNamespace(get_by_kb_id=AsyncMock(return_value=kb))
    chunk_repo = SimpleNamespace(
        count_by_kb_id=AsyncMock(return_value=11_017),
        count_graph_pending_by_kb_id=AsyncMock(return_value=0),
        count_graph_indexed_by_kb_id=AsyncMock(return_value=11_017),
    )
    graph_repo = SimpleNamespace(count_by_kb_id=AsyncMock(return_value=(5_316, 11_506)))
    tasker = SimpleNamespace(
        find_task_by_payload=AsyncMock(
            side_effect=[None, SimpleNamespace(status="failed", progress=100)]
        )
    )
    service = MilvusGraphService(kb_repo=kb_repo, chunk_repo=chunk_repo, graph_repo=graph_repo)

    status = await service.get_status("kb_test", tasker=tasker)

    assert status["pending_chunks"] == 0
    assert status["indexed_chunks"] == 11_017
    assert status["build_task_status"] is None


@pytest.mark.asyncio
async def test_graph_build_continues_after_failed_chunks_fill_a_batch():
    chunks = [SimpleNamespace(chunk_id=f"chunk_{index}", file_id="file_1") for index in range(3)]

    class ChunkRepo:
        def __init__(self):
            self.indexed: set[str] = set()

        async def count_graph_pending_by_kb_id(self, kb_id):
            return len(chunks) - len(self.indexed)

        async def list_graph_pending_by_kb_id(self, kb_id, limit, exclude_chunk_ids=None):
            excluded = exclude_chunk_ids or set()
            return [chunk for chunk in chunks if chunk.chunk_id not in self.indexed | excluded][:limit]

        async def mark_graph_indexed(self, chunk_id, ent_ids=None):
            self.indexed.add(chunk_id)

    chunk_repo = ChunkRepo()
    graph_repo = SimpleNamespace(upsert_chunk_graph=AsyncMock())
    graph_vector_store = SimpleNamespace(insert_missing_graph_records=AsyncMock())
    service = MilvusGraphService(
        chunk_repo=chunk_repo,
        graph_repo=graph_repo,
        graph_vector_store=graph_vector_store,
    )
    service._get_milvus_kb = AsyncMock(
        return_value=SimpleNamespace(
            additional_params={
                "graph_build_config": {
                    "locked": True,
                    "extractor_type": "llm",
                    "extractor_options": {"model_spec": "test/model", "concurrency_count": 1},
                }
            },
            embedding_model_spec="test/embed",
        )
    )
    service._get_chunk_extraction_result = AsyncMock(
        side_effect=[RuntimeError("timeout"), {"entities": [], "relations": []}, {"entities": [], "relations": []}]
    )
    service.write_chunk_graph = MagicMock(return_value=([], []))

    result = await service.build_pending_chunks("kb_test", batch_size=1)

    assert result == {"kb_id": "kb_test", "success": 2, "failed": 1, "remaining": 1}
    assert chunk_repo.indexed == {"chunk_1", "chunk_2"}


def test_milvus_graph_service_writes_chunk_entity_and_relation():
    tx = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute_write.side_effect = lambda func: func(tx)
    driver = MagicMock()
    driver.session.return_value = session
    connection = SimpleNamespace(driver=driver)
    service = MilvusGraphService(neo4j_connection=connection)
    chunk = SimpleNamespace(
        chunk_id="chunk_1",
        file_id="file_1",
        kb_id="kb_test",
        chunk_index=1,
        content="张三任职于公司",
        start_char_pos=0,
        end_char_pos=8,
    )

    entities, triples = service.write_chunk_graph(
        "kb_test",
        chunk,
        normalize_extraction_result(
            {
                "relations": [
                    {
                        "source": {
                            "text": "张三",
                            "label": "Person",
                            "attributes": [{"text": "工程师", "label": "Occupation"}],
                        },
                        "target": {"text": "公司", "label": "Organization"},
                        "text": "任职于",
                        "label": "WORKS_AT",
                    }
                ],
            },
            "llm",
        ),
    )

    assert [entity["name"] for entity in entities] == ["张三", "公司"]
    assert {entity["label"] for entity in entities} == {"Person", "Organization"}
    assert triples[0]["relation_type"] == "WORKS_AT"
    queries = [call.args[0] for call in tx.run.call_args_list]
    assert any("MERGE (c:Chunk:MilvusKB:`kb_test`" in query for query in queries)
    assert any("MERGE (e:Entity:MilvusKB:`kb_test`" in query for query in queries)
    assert any("MERGE (source)-[r:RELATION" in query for query in queries)
    entity_call = next(call for call in tx.run.call_args_list if "MERGE (e:Entity" in call.args[0])
    assert entity_call.kwargs["attributes"] == '[{"text": "工程师", "label": "Occupation"}]'


def test_milvus_graph_service_delete_file_graph_uses_scoped_streaming_queries():
    tx = MagicMock()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute_write.side_effect = lambda func: func(tx)
    driver = MagicMock()
    driver.session.return_value = session
    service = MilvusGraphService(neo4j_connection=SimpleNamespace(driver=driver))

    service._delete_file_graph_from_neo4j("kb_test", "file_1")

    queries = [call.args[0] for call in tx.run.call_args_list]
    assert len(queries) == 3
    cleanup_query = queries[1]
    assert "file_id: $file_id" in cleanup_query
    assert "DELETE m" in cleanup_query
    assert "WITH DISTINCT e" in cleanup_query
    assert "collect(" not in cleanup_query
    assert "MATCH (e:Entity:MilvusKB:`kb_test` {kb_id: $kb_id})" not in cleanup_query
    assert "DETACH DELETE c" in queries[2]


def test_milvus_graph_service_process_query_result_keeps_complete_edges():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=2,
        kb_id="kb_test",
    )

    assert [node["id"] for node in result["nodes"]] == ["node-a", "node-b"]
    assert [edge["id"] for edge in result["edges"]] == ["edge-a-b"]


def test_milvus_graph_service_process_query_result_filters_edges_after_node_limit():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=1,
        kb_id="kb_test",
    )

    assert [node["id"] for node in result["nodes"]] == ["node-a"]
    assert result["edges"] == []


def test_milvus_graph_service_process_query_result_filters_edges_to_excluded_chunk_nodes():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("entity-a"),
                "t": _raw_graph_node("chunk-a", labels=["MilvusKB", "Chunk"]),
                "r": _raw_graph_edge("edge-entity-chunk", "entity-a", "chunk-a"),
            }
        ],
        limit=2,
        kb_id="kb_test",
        exclude_chunk=True,
    )

    assert [node["id"] for node in result["nodes"]] == ["entity-a"]
    assert result["edges"] == []


def test_milvus_graph_service_process_query_result_clamps_negative_limit():
    service = MilvusGraphService()
    result = service._process_query_result(
        [
            {
                "h": _raw_graph_node("node-a"),
                "t": _raw_graph_node("node-b"),
                "r": _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            }
        ],
        limit=-1,
        kb_id="kb_test",
    )

    assert result == {"nodes": [], "edges": []}


@pytest.mark.parametrize("max_depth", [1, 2, 3])
def test_milvus_graph_service_build_query_uses_requested_depth(max_depth):
    service = MilvusGraphService()

    query = service._build_query("kb_test", "entity", limit=20, max_depth=max_depth)

    assert f"[*1..{max_depth}]" in query
    assert "nodes(path)" in query
    assert "relationships(path)" in query
    assert "nodes(path)) + seeds AS nodes" in query


def test_milvus_graph_service_build_query_excludes_chunks_from_entire_path():
    service = MilvusGraphService()

    query = service._build_query("kb_test", "entity", limit=20, max_depth=3, exclude_chunk=True)

    assert "NOT n:Chunk" in query
    assert "NOT path_node:Chunk" in query


def test_milvus_graph_service_query_nodes_sync_returns_complete_multi_hop_path():
    query_result = MagicMock()
    query_result.single.return_value = {
        "nodes": [_raw_graph_node("node-a"), _raw_graph_node("node-b"), _raw_graph_node("node-c")],
        "edges": [
            _raw_graph_edge("edge-a-b", "node-a", "node-b"),
            _raw_graph_edge("edge-b-c", "node-b", "node-c"),
        ],
    }
    session = MagicMock()
    session.__enter__.return_value = session
    session.run.return_value = query_result
    driver = MagicMock()
    driver.session.return_value = session
    service = MilvusGraphService(neo4j_connection=SimpleNamespace(driver=driver))

    result = service._query_nodes_sync(
        "kb_test",
        "kb_test",
        "node-a",
        limit=3,
        max_depth=2,
        exclude_chunk=False,
    )

    assert [node["id"] for node in result["nodes"]] == ["node-a", "node-b", "node-c"]
    assert [edge["id"] for edge in result["edges"]] == ["edge-a-b", "edge-b-c"]
    query, query_params = session.run.call_args
    assert "[*1..2]" in query[0]
    assert query_params["path_limit"] == 30


def test_milvus_graph_service_query_nodes_sync_caps_max_depth():
    query_result = MagicMock()
    query_result.single.return_value = None
    session = MagicMock()
    session.__enter__.return_value = session
    session.run.return_value = query_result
    driver = MagicMock()
    driver.session.return_value = session
    service = MilvusGraphService(neo4j_connection=SimpleNamespace(driver=driver))

    service._query_nodes_sync(
        "kb_test",
        "kb_test",
        "node-a",
        limit=3,
        max_depth=100,
        exclude_chunk=False,
    )

    query, _ = session.run.call_args
    assert "[*1..3]" in query[0]


@pytest.mark.asyncio
async def test_milvus_graph_service_query_nodes_empty_kb_id():
    service = MilvusGraphService()
    result = await service.query_nodes(kb_id=None, keyword="test")
    assert result == {"nodes": [], "edges": []}


@pytest.mark.asyncio
async def test_milvus_graph_service_get_labels_empty_kb_id():
    service = MilvusGraphService()
    result = await service.get_labels(kb_id=None)
    assert result == []


@pytest.mark.asyncio
async def test_milvus_graph_service_get_stats_empty_kb_id():
    service = MilvusGraphService()
    result = await service.get_stats(kb_id=None)
    assert result == {"total_nodes": 0, "total_edges": 0, "entity_types": []}
