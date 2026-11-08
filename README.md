# AllFusion

统一 Agent 平台：问数（Text-to-SQL / 写操作审批）、知识库 RAG 问答、租户与权限管理。

## 服务与端口

| 服务 | 目录 | 端口 | 说明 |
| --- | --- | --- | --- |
| unified_agent | `unified_agent/` | 8000 | 统一入口：路由分诊、RAG / 问数车道编排 |
| doc_parser | `doc_parser/` | 8002 | 文档解析 |
| kb_service | `kb_service/` | 8003 | 知识库：灌库、分层切块、混合检索 |
| auth_service | `auth_service/` | 8004 | 登录、租户、用户与权限、数据源 |
| query_gateway | `query_gateway/` | 8130 | SQL 网关：只读查询 / 写操作预览与执行 |
| cherry-ui | `cherry-ui/` | 5173（开发） | 前端 |
| nginx | `deploy/nginx/` | 8080（生产） | 静态站点 + `/api` 反向代理 |

## 环境要求

- Windows（启动脚本为 PowerShell）
- Python 3.10+
- Node.js 18+
- PostgreSQL 16（pgvector 扩展；向量库与 LangGraph checkpoint）
- MySQL 5.7+（元数据、租户、权限）
- ClickHouse 24.x（可选，问数分析通道）
- Kafka + Kafka Connect（可选，CDC 镜像）
- nginx（可选，生产静态托管）

## 配置

复制模板并填写：

```powershell
Copy-Item .env.example .env
```

关键变量：

- `AF_SECRET_KEY`：Fernet 密钥（用于加密数据源密码），必填
- `AF_MYSQL_DSN`：平台库连接串
- `AF_VECTOR_DSN`：pgvector 连接串，`AF_VECTOR_DIM=512` 对应 `bge-small-zh-v1.5`
- `AF_CH_URL`：ClickHouse 地址
- `AF_KAFKA_CONNECT` / `AF_KAFKA_BROKERS`：CDC 镜像
- `AF_GATEWAY_SECRET`：内部服务 HMAC 签名密钥，`query_gateway/.env` 与
  `DataAnalyzeAgent/DataAnalyzeAgent/.env` 必须一致

生产环境建议：`AF_REGISTER_ENABLED=0`、`AF_COOKIE_SECURE=1`、
`AF_ALLOWED_ORIGINS` 填实际域名、`AF_ORIGIN_STRICT=1`。

## 启动

先确保 PostgreSQL（含 pgvector）、MySQL 已运行；用到问数/追踪时再起 ClickHouse、Kafka。
根目录 `.env` 配好后，每个服务单独开一个终端，按顺序启动：

```powershell
# 仓库根目录
python -m auth_service.app.main      # 认证与权限   8004
python -m kb_service.api.main        # 知识库       8003
python -m unified_agent.api.agent    # 统一入口     8000

# query_gateway 目录
python -m app.main                   # SQL 网关     8130

# doc_parser 目录
python -m app.main                   # 文档解析     8002
```

前端：

```powershell
cd cherry-ui
npm install
npm run dev                          # 开发服务器 5173，已代理到 8000 / 8003 / 8004
```

生产部署：

```powershell
cd cherry-ui
npm run build                        # 产物在 cherry-ui/dist
```

构建后由 nginx 加载 `deploy/nginx/allfusion.conf`：监听 8080，静态目录指向
`cherry-ui/dist`，`/api` 反向代理到上面的服务。

## 访问

生产环境走 nginx：`http://127.0.0.1:8080/`；前端开发模式：`http://127.0.0.1:5173/`。

## 数据库初始化

按顺序执行：

1. `docs/mysql/*.sql`（平台库、账号、数据源、CDC、权限）
2. `docs/clickhouse/001_init.sql`（trace 库与业务分析表）
3. `query_gateway/sql/*.sql`（问数视图与 CDC DDL，可选）

## 权限模型

单租户多用户：`tenant` 下分 `owner` / `db_admin` / `member`。
租户所有者可管理成员与重置密码；数据源镜像与授权由 `db_admin` 维护。

## 许可证

[MIT](LICENSE)
