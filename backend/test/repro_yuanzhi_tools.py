# 临时验证脚本：真实 ChatbotAgent 链路下，yuanzhi-m1 的文本工具调用
# 是否被正确还原为原生 tool_calls 并真正执行（list_kbs -> 检索 -> 回答）。
import asyncio
import uuid


async def main():
    from yuxi.agents.buildin.chatbot.graph import ChatbotAgent

    agent = ChatbotAgent()
    thread_id = f"repro-kb-{uuid.uuid4().hex[:8]}"
    workdir_rel = f"projects/repro-{uuid.uuid4().hex[:8]}"
    input_context = {
        "thread_id": thread_id,
        "uid": "admin",
        "model": "YuanZhi:yuanzhi-m1",
        "tool_approval_mode": "default",
        "runtime_scope_id": thread_id,
        "workdir_relative_path": workdir_rel,
        "workdir_path": f"/app/user-data/{workdir_rel}",
        "tools": ["install_skill", "present_artifacts", "ocr_parse_file", "ask_user_question"],
        "knowledges": ["kb_9e874tm224"],
        "mcps": [],
        "skills": ["knowledge-base", "deep-research"],
        "preload_skills": ["knowledge-base", "deep-research"],
        "subagents": [],
    }

    print(f"=== thread: {thread_id} ===")
    counts = {}
    question = "请先用工具查询知识库，再基于知识库检索到的内容回答临床输血技术规范。不要凭你自己的知识直接回答。"
    async for kind, payload in agent.stream_messages_with_state(
        [question], input_context
    ):
        counts[kind] = counts.get(kind, 0) + 1
        if kind != "messages":
            if kind == "values":
                vals = payload.get("messages", []) if isinstance(payload, dict) else []
                for m in vals:
                    if isinstance(m, dict):
                        mt, mc, mtc = m.get("type"), m.get("content"), m.get("tool_calls")
                    else:
                        mt = getattr(m, "type", None)
                        mc = getattr(m, "content", "")
                        mtc = getattr(m, "tool_calls", None)
                    if mt in ("ai", "tool"):
                        print(f"[VALUES-MSG] type={mt} content={repr(mc)[:200]} tool_calls={mtc}")
            continue
        msg, _meta = payload
        if isinstance(msg, dict):
            mtype = msg.get("type")
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or []
        else:
            mtype = getattr(msg, "type", None)
            content = getattr(msg, "content", "") or ""
            tool_calls = getattr(msg, "tool_calls", None) or []
        if mtype == "ai":
            names = [tc.get("name") for tc in tool_calls if isinstance(tc, dict)]
            print(f"[AI] tool_calls={names} content={repr(content)[:160]}")
            assert "<tool_call>" not in str(content), "content 中不应再残留 tool_call 文本"
        elif mtype == "tool":
            print(f"[TOOL] content={repr(content)[:160]}")
        else:
            print(f"[{mtype}] {repr(content)[:80]}")

    print(f"=== 事件统计: {counts} ===")
    print("=== 完成，无异常 ===")


asyncio.run(main())
