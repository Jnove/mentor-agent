# 百炼校园政策问答 PoC

这个目录是独立的最小实现，不使用项目原有的 Chroma、BM25、embedding 或 reranker。

## 1. 配置百炼

1. 在阿里云百炼控制台创建知识库并上传资料。
2. 创建并发布知识问答服务，记录 `aid-...` 格式的 Agent ID。
3. 复制 `.env.example` 为 `.env`，填写控制台导出的凭据 CSV 路径和 Agent ID。

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

当前只包含问答、多轮会话、来源片段展示和清空对话。对话历史由本地页面传给百炼；知识库上传与更新暂时使用百炼控制台完成。

## 4. 生成本地上传清单

下面的命令只扫描本地文件，不调用百炼接口：

```powershell
python -m bailian_agent.upload_manifest --knowledge-base-id o2jmpen3eu
```

结果保存在 `bailian_agent/upload_manifest.jsonl`。相同 `doc_id` 只保留一条，正式目录优先于 `staging`；内容哈希用于判断是否需要重新上传。

## 5. 上传知识库

上传器使用阿里云官方 SDK，并在每次成功后更新同一个清单。重复运行会从中断位置继续：

```powershell
python -m bailian_agent.upload_kb `
  --access-key-csv "C:\Users\Vito\Downloads\AccessKey.csv" `
  --workspace-id ws-gdu6o99wwwsr7y6n `
  --index-id o2jmpen3eu
```

参考：[百炼应用 API](https://help.aliyun.com/zh/model-studio/call-alibaba-cloud-model-studio-through-api)、[官方 Python SDK](https://github.com/dashscope/dashscope-sdk-python)。本 PoC 直接使用项目已有的 `httpx`，不额外引入 SDK。
