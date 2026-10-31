# 外接 Agent 接口

`core.agent_bridge` 提供进程内、显式启用的适配器边界。默认没有绑定，所有管线继续使用已有模型调用。当前没有实现 DSH 或 Codex 适配器，也不会自动查找或启动外部程序。

可信的应用代码可以实现 `complete(request)`，通过 `register_adapter(name, adapter)` 注册，再调用 `bind_adapter(pipeline, name)` 将它绑定到指定管线；传 `None` 可恢复原模型调用。绑定只存在于当前进程。若已绑定的适配器报错，错误会返回给管线，不会静默切回模型并产生另一笔调用。

`AgentRequest` 包含管线名称、消息、模型提示、输出上限、推理设置、温度、超时及可选的响应格式；其中 `max_tokens=0` 表示**无显式上限**（看板设置里该管线的上限为 0 时，请求里不发送 `max_tokens`；正数才是该次请求的输出上限）；它不包含 API 密钥或服务地址。`complete` 返回 `{"content": "...", "model": "...", "usage": {...}, "finish_reason": "..."}`，其中 `finish_reason` 可选；适配器应如实报告 `length` 等截断状态。可选的 `stream(request)` 返回 `(kind, value)` 事件，kind 为 `delta`、`usage` 或 `done`；没有流式方法时会把完整响应作为一个增量发送。图片输入位于消息内容中，视觉适配器必须支持相应图片格式。

现有路由：`chat`（知识库问答）、`deck_chat`（课件答疑）、`ingest_vision`（入库识图）、`ingest_outline`（入库结构分析）、`structures`（结构式分析）、`courseware_kp`（课件知识点）、`terms`（名词整理）、`preview`（教材预习）、`textbook_kp`（教材知识点）和 `textbook_teach`（教材补充答疑）。调用上下文通过线程上下文传递；课件知识点的超时陪跑线程也保留该上下文。

未来接 DSH 或 Codex 时，需要单独实现适配器与用户可见的选择、授权、取消、超时和用量呈现。资料正文与图片会作为消息发送给被选中的适配器，应明确告知使用者；模型或 Agent 输出仍需经过管线自己的 JSON、来源和内容校验。不要把密钥写入消息、日志或产物，也不要依据文档内容拼接 shell 命令。


## 能力声明（P4，默认关闭）

适配器可选实现 `capabilities()` 返回 `AgentCapabilities`（或同字段 dict）：

```python
from core.agent_bridge import AgentCapabilities

class MyAgent:
    def capabilities(self):
        return AgentCapabilities(
            pipelines=frozenset({"chat", "textbook_kp"}),
            stream=False, images=False, json=True, usage=True)
```

- `pipelines=None`（或空集合）= 旧适配器兼容：可绑定全部已有管线；
- `stream=False` 且没有 `stream()`：`stream_chat` 把一次 `complete()` 响应降级成
  `delta/usage/done` 事件（显式兼容旧语义，不会自动改走其它服务）；
- `images=False`：图片请求在发送前明确报错（不把图片/正文发给不支持者，不 fallback 原模型）；
- `json=False`：`response_format` JSON 输出请求明确报错；未声明 capabilities 的旧适配器保持
  旧行为（不新增拒绝）；
- 绑定（`bind_adapter`）到适配器未声明的管线会**明确拒绝**，路由保持原样，不会静默 fallback
  到原付费模型；适配器内部异常原样上抛。
- 没有绑定任何适配器时行为与改造前完全一致（走原模型路径）；不安装/启动 DSH/Codex，
  不真实调用任何外部模型。
