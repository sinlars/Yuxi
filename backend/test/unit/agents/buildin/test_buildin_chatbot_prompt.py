from types import SimpleNamespace

from yuxi.agents.buildin.chatbot.prompt import build_prompt_with_context


def test_chatbot_prompt_prioritizes_configured_knowledge_base_and_keeps_citations_last() -> None:
    prompt = build_prompt_with_context(
        SimpleNamespace(
            system_prompt="自定义要求",
            knowledges=["制度知识库"],
        )
    )

    assert "应先使用 knowledge-base Skill 检索知识库" in prompt
    assert "正文应至少各引用一个来源" in prompt
    assert "正文使用了网络结果中独有的信息时" in prompt
    assert prompt.index("自定义要求") < prompt.index("<| 引用来源 |>")
    assert prompt.rstrip().endswith("`学校实行数据分类分级管理。<cite source=\"kb://example/file?chunk=abc\"></cite>`")


def test_chatbot_prompt_does_not_force_knowledge_base_without_configuration() -> None:
    prompt = build_prompt_with_context(SimpleNamespace(system_prompt="普通对话", knowledges=[]))

    assert "<| 知识库与网络检索顺序 |>" not in prompt
    assert "<| 引用来源 |>" in prompt
    assert "若某一类结果未提供额外有效证据，应明确说明未采用原因" in prompt
