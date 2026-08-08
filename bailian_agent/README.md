# 百炼校园政策问答 PoC

这个目录是独立的最小实现，不使用项目原有的 Chroma、BM25、embedding 或 reranker。

## 1. 配置百炼

1. 在阿里云百炼控制台创建知识库并上传资料。
2. 创建应用、关联知识库，开启“展示回答来源”，然后发布应用。
3. 复制 `.env.example` 为 `.env`，填写 API Key 和应用 ID。

## 2. 安装与启动

在仓库根目录运行：

```powershell
pip install -r bailian_agent/requirements.txt
streamlit run bailian_agent/app.py
```

## 3. 测试

```powershell
python -m unittest bailian_agent.test_client
```

当前只包含问答、多轮会话、来源片段展示和清空对话。知识库上传与更新暂时使用百炼控制台完成。

参考：[百炼应用 API](https://help.aliyun.com/zh/model-studio/call-alibaba-cloud-model-studio-through-api)、[官方 Python SDK](https://github.com/dashscope/dashscope-sdk-python)。本 PoC 直接使用项目已有的 `httpx`，不额外引入 SDK。
