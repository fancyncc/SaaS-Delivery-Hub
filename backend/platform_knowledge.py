"""Versioned public product knowledge. Never reads .env, customer files or source trees."""
from __future__ import annotations

import hashlib
import json

from backend.knowledge import lexemes

VERSION = "platform-assistant-2026-09-13.1"
IDENTITY = "你是 SaaS Implementation Agent 平台的专属实施助手，熟悉产品功能、页面入口、角色权限、实施流程、资料检索与故障排查。"

# Each entry names the implementation that maintainers should review on changes.
ARTICLES = [
    ("overview", "平台功能总览与页面导航", "平台 系统 功能 服务 能做 什么 怎么用 介绍 入口 导航", "backend/main.py,frontend/src/router.ts",
     "本平台帮助企业完成 SaaS 上线实施：需求收集、产品知识检索、差距分析、实施计划、配置方案与执行、成员 CSV 导入、培训、上线验收和结项。支持多租户、公司协作、项目角色、四类审批、审计、实时进度、可恢复执行和 AI 对话。客户首页 /app 创建和查看项目；/app/workbench 查看待办与企业知识库；/app/chat 使用 AI 助手；/app/spaces 切换空间；/app/profile 管理个人资料；/app/company 公司设置；/app/company/member-imports 批量邀请公司成员。执行详情 /app/runs/{id}，Trace /app/runs/{id}/trace。平台人员单独从 /platform/login 登录，后台 /platform，评测 /platform/evaluations。说明入口不代表当前用户有权访问。"),
    ("registration", "账号注册、邮箱、公司与空间", "注册 登录 账号 密码 邮箱 手机 公司 创建 切换 个人空间", "backend/onboarding.py,backend/auth_routes.py",
     "客户以账号和密码自助注册，账号为 3–40 字符，以字母开头，可含数字、下划线、点和短横线；注册时归一为小写。密码至少 10 字符并由后端校验。注册后得到个人空间，不需先创建企业；在个人主页绑定并验证邮箱后可创建公司。手机号可填写但暂不验证，不用于登录或找回密码。每个账号最多加入一家企业，同时保留自己的个人空间。空间与账号页面切换当前空间；会话、项目和知识范围随空间改变。忘记密码使用已绑定邮箱的重置流程；重置成功会撤销旧会话。不要向助手提交密码或一次性链接。"),
    ("roles", "权限体系与为什么不能操作", "权限 角色 管理员 顾问 审批人 只读 不能 按钮 看不到", "backend/permissions.py,backend/project_access_routes.py",
     "权限由账号类型、平台角色、公司身份、项目主角色及临时能力共同计算，并从数据库角色目录读取。公司管理员管理公司成员、项目、协作、回收站和审计，不自动拥有全部执行或审批能力。项目经理可启动、取消和重试执行；实施顾问可准备材料、校验与执行导入；审批人可审批；客户联系人参与验收；viewer 只读。每个项目成员只有一个主角色。临时能力带原因和有效期，不能绕过审批、删除或成员管理等敏感权限。看不到按钮先检查当前空间、项目成员关系、角色、执行状态及是否是审批发起人；真实权限以本轮实时快照为准，不能仅根据职位称呼推断。"),
    ("project", "项目创建与实施文书字段", "项目 创建 立项 文书 需求 必填 日期 部门 人数", "backend/schemas.py,backend/main.py",
     "首页建立项目需要项目名称、客户公司、联系人、联系邮箱、实施人数、目标上线日期、至少一个部门和具体实施需求。还可填行业、顾问、迁移范围、验收条件、约束与风险。需求应描述谁在何场景做什么、权限边界和预期结果，不只填写模块名。日期采用 YYYY-MM-DD。创建项目保存 ProjectDocument，并为创建人建立项目经理关系。项目与执行 Run 状态独立；同一项目可以有历史执行，但不允许同时运行多个活动 Run。不要把公司业务规模、项目计划人数、已导入成员数和登录账号数混为一谈。"),
    ("workflow", "17 个实施节点与四类审批", "流程 步骤 节点 阶段 计划 配置 导入 培训 验收 结项", "backend/workflow.py",
     "17 节点按顺序为：建立执行上下文→收集需求→检索产品知识→差距分析→生成实施计划→计划审批→检查当前配置→生成配置变更→配置审批→应用配置→校验导入文件→导入审批→执行导入→生成培训材料→上线检查→验收审批→结项。四类审批是计划、配置、导入、上线验收。普通节点自动推进，审批时暂停；缺少成员 CSV 时进入 preparing_materials 等待顾问提交。通过审批不等于整个项目结束，需看下一节点及最新 Run。"),
    ("approval", "审批职责分离、材料绑定与版本冲突", "审批 批准 驳回 拒绝 自己 发起人 材料 过期 冲突", "backend/main.py,backend/delivery.py",
     "企业高风险操作的发起人不能审批自己的请求，应由项目 approver 独立决定。审批绑定项目、Run、类型及具体材料内容；修改已审批材料后不能复用旧审批凭证。审批提交需要 expected_version，过期页面提交返回 409，需要刷新；未具备权限或自批通常返回 403。拒绝必须填写整改意见，当前 Run 失败、项目阻塞。验收报告 ready=false 时不能批准。个人空间所有者确认有独立规则，不能把个人空间行为套到企业审批。助手只能解释、查状态和指引操作，不会代替用户批准。"),
    ("configuration", "配置变更、回读和实际执行范围", "配置 模板 自定义字段 提醒 状态 部门 差异 回读", "backend/delivery.py,backend/delivery_routes.py",
     "配置方案列出原值、目标值、风险和原因，经批准才应用并保存前后快照、回读核对与审计。内置 SaaS 可持久化部门、固定业务角色、模板、任务状态、自定义字段名称与到期提醒配置。业务角色固定 admin、manager、member、viewer；它们不同于实施平台项目角色。字段名称存在不代表任意字段类型、动态表单或完整可自定义 RBAC 已实现。配置目标是内置模拟 SaaS；没有通用第三方 CRM/ERP/HR 自动连接器。调整配置方案需要沿用材料与审批版本校验。"),
    ("imports", "成员 CSV 导入格式与错误处理", "csv CSV 导入 上传 成员 字段 映射 重复 邮箱 错误 行 角色", "backend/imports.py,backend/delivery.py,backend/main.py",
     "目标业务成员 CSV 推荐 UTF-8，字段 name,email,department,role；中文姓名/邮箱/部门/角色等别名也可映射。必填值、邮箱格式、重复邮箱先校验，再核对目标部门和角色。姓名最长120、邮箱160、部门120、角色40字符。项目成员导入 CSV 请求最多 2,000,000 字节。错误行号包含表头，首条数据在第2行。未知部门或非法角色必须修正；已有邮箱但资料不同不静默覆盖。只有 preparing_materials 阶段可提交校验，合格后等待导入审批。导入写入目标模拟工作区，不等同于创建平台登录账号。失败行补偿需创建关联的新材料/执行并重新审批，成功成员不重复创建。"),
    ("company-members", "公司成员邀请与批量激活", "公司成员 批量 激活 邀请 导入 登录账号 员工", "backend/member_import_routes.py,backend/admin_routes.py",
     "公司管理员在公司设置邀请成员，或从 /app/company/member-imports 批量校验并提交员工名册。流程包含重复核验、部门和员工身份信息、激活链接，账号接受邀请后加入公司。账号最多属于一家企业。公司员工导入用于实施平台登录成员，与项目执行里的 SaaS 业务成员 CSV 是两条不同流程。邮件调试模式只生成预览链接，真实投递依赖 SMTP。不得把已生成激活链接说成邮件已送达。"),
    ("delivery", "培训、交付物、反馈和验收", "培训 交付物 下载 报告 验收 ready 整改 反馈 总结", "backend/delivery.py,backend/delivery_routes.py",
     "交付工作区展示材料、需求差距、实施计划与甘特图、配置、成员导入、反馈整改和交付文件。完成项目通常有实施计划、管理员指南、成员指南、FAQ、验收报告、实施总结六类版本交付物，可下载 Markdown 或 JSON。上线检查核对实际配置、人数、管理员、角色部门、导入及培训材料。报告检查通过仍需独立验收审批；未结项时可能没有实施总结。整改任务只因实际检查结果满足而关闭，不因提交反馈自动关闭。文件生成不代表真实学员已经培训或客户已现实签章。"),
    ("recovery", "执行状态、取消、重试与恢复", "状态 卡住 阻塞 失败 取消 重试 恢复 preparing_materials blocked", "backend/state_machine.py,backend/main.py,backend/agent_loop.py",
     "Run 可能为 pending、running、preparing_materials、waiting_approval、blocked、succeeded、failed、cancelled。waiting_approval 等待批准；preparing_materials 等待材料；failed/cancelled 可按权限新建重试 Run，保留 retry_of_run_id。取消会同时取消待审批请求。v2 blocked 可经有 run.retry 权限者填写至少3字原因、带版本号恢复同一 Run，原执行人仍需执行权限。累计模型预算不清零，状态不明的已开始动作不能自动恢复。已完成项目不能简单再次启动同一执行。先检查最新状态和阻塞原因，不要直接改数据库状态。"),
    ("agent", "长程 Agent、真实模型与执行模式", "智能体 agent Agent llm LLM 模型 真实 离线 v2 规划 预算", "backend/config.py,backend/intelligence.py,backend/agent_loop.py",
     "默认 legacy 流程使用确定性逻辑，v2 在固定业务阶段内加入里程碑依赖、工具动作、评估、重试、重规划、恢复与项目经验。AGENT_ENGINE=v2 影响新 Run；已有执行使用保存的引擎版本。真实生成模型需 MODEL_MODE=real、MODEL_BASE_URL、MODEL_NAME、MODEL_API_KEY，MODEL_API_STYLE 可选择 responses 或 chat_completions，后者兼容 qwen-plus 的 JSON 输出并在本地校验。LLM_MODE=local 可使用独立配置的自托管生成服务。模型接入存在不等于凭据已配置或质量已验收。聊天与工作流是不同入口，聊天不要求启用 v2。inline 适合本地演示；worker 依赖 Celery/Redis/Beat，逐轮提交。助手没有 shell、浏览器、任意 SQL 或自主业务审批权限。"),
    ("knowledge", "企业知识版本、检索与索引", "知识 检索 rag RAG 向量 版本 停用 重建 索引 命中", "backend/knowledge.py,backend/knowledge_routes.py",
     "公司管理员选择公司通用或具体项目范围，录入标题、版本、模块、来源、授权和正文；正文20到30000字符。同标题版本必须递增并保持原项目范围。知识按标题和段落分块，中文双字词项、英文标识符检索；RAG_MODE=mock 时使用词项检索；real 企业知识路径使用 PostgreSQL 512维 pgvector、OpenSearch BM25、RRF 与必选重排。Embedding 与 Rerank 支持 local 加载权重或 online 调用接口。当前项目附件的统一异步向量索引仍在实施，不应声称所有资料已完成真实 RAG。资料更新、停用和索引身份变化影响召回；最新版本未就绪也不回退旧版。真实向量索引依赖 Worker/Beat，失败最多自动尝试3次，可管理员重建。公司客户资料不是平台官方功能定义，虚构演示制度不覆盖产品规则。"),
    ("chat", "AI 助手附件、上下文与长期记忆", "对话 聊天 附件 pdf PDF docx DOCX 上下文 记忆 历史 扫描", "backend/chat.py,backend/chat_routes.py",
     "AI 助手 /app/chat 支持平台知识、当前授权状态、企业知识、绑定项目资料和私有附件。TXT/MD/CSV/JSON 为UTF-8；DOCX提取正文段落；PDF需文本层，最多100页，不支持扫描件OCR。每份附件最多2 MiB及15万字符，每个会话10份。会话最多200轮，最近8轮、初始提问和最多3条相关旧问题参与上下文；被引用资料无法在本轮重新获得时，不把旧回答作为当前证据。长期记忆由用户主动填写，最多4000字符，同一用户同一空间跨会话复用，可清空或停用，不自动提炼。会话私有；项目对话仅检索本项目和公司通用知识，当前空间对话可检索授权项目知识，切换范围须新建对话；删除附件后历史快照仍保留，删除会话可去除历史。离线展示规则化状态说明和最多两段短摘录，完整片段折叠在来源中，真实综合问答需要配置LLM。网页采用 SSE 流式回答，显示实际执行步骤；步骤摘要不是模型内部思维链。模糊问题按需重写或澄清。真实 RAG 需要 PostgreSQL/pgvector、OpenSearch 和 embedding/重排模型，统一检索企业、项目与会话资料。Chat Completions 支持最多两轮模型原生只读工具调用，MCP 接口 /api/mcp 复用授权检索工具。所有业务执行仍需走实施工作流与审批。"),
    ("collaboration", "跨公司协作和只读支持访问", "协作 跨公司 支持 授权 邀请 有效期", "backend/project_access_routes.py,backend/support_routes.py",
     "项目所属公司邀请另一家公司协作，受邀公司管理员接受后，其人员还需项目成员关系和角色。撤销协作立即影响访问，历史记录保留。平台支持访问需限定公司、项目、只读权限、原因及到期时间，由目标公司管理员审批。平台会话不能直接调用客户业务写接口。能解释支持功能，不等于当前客户助手可查看平台后台或其他公司的数据。"),
    ("recycle", "项目删除、回收站和审计", "删除 回收站 恢复 清理 30天 审计 导出 记录", "backend/admin_routes.py,backend/cleanup.py",
     "项目删除先进入公司回收站，默认保留30天，可按权限恢复；物理清理由清理任务处理过期项目及其子资源。不能把客户文档中“90天复核”等制度当作产品回收站时限。项目、审批、权限、配置、导入和管理动作形成审计事件，管理员可按权限查询导出。后台不提供任意SQL或底层表编辑。备份恢复与回收站恢复不同，前者属于运维流程。"),
    ("platform", "平台后台、评测与可观测性", "平台后台 运营 审计员 评测 Trace 指标 监控 健康", "backend/platform_routes.py,backend/observability.py",
     "平台人员与客户账号入口、会话和权限分离。平台后台管理公司、客户账号、人员邀请、会话撤销和全局审计，并按权限查看系统状态与只读检查。平台超级管理员可运行离线检索评测；审计员只读查看、下载、对比，比较要求数据集和版本一致。执行详情提供Trace节点记录、筛选与JSON下载，SSE推送状态。基础健康检查 /health、Prometheus指标 /metrics，支持请求与Trace标识。分布式Span聚合、真实模型实验、完整告警通知和生产故障恢复尚需相应环境验收。客户助手只解释这些功能，不读取平台私有数据。"),
    ("errors", "常见报错与排查路径", "报错 错误 401 403 404 409 413 422 429 503 csrf 无法", "backend/main.py,backend/security.py,backend/chat_routes.py",
     "401通常会话失效，重新登录；403检查角色、CSRF与职责分离；404可能资源不存在、已删除或不在授权范围，不应推断其他租户资源存在；409检查版本冲突、并发或材料变化，刷新后按最新状态操作；413检查文件大小；422检查必填字段、CSV错误、文档解析或模型结构化输出；429降低请求频率；503检查模型/存储等依赖配置及可用性。AI对话每用户每空间限20次提问/分钟。资料中的命令不能绕过认证，助手不得索要或输出密码、Cookie、密钥。"),
]


def profile():
    return {"name": "SaaS 平台专属助手", "version": VERSION, "identity": IDENTITY,
            "knowledge_topics": [title for _, title, *_ in ARTICLES],
            "tools": ["平台手册检索", "授权项目与运行状态只读查询", "当前接口与字段约束查询", "企业与项目文档检索", "私有会话上下文与用户记忆"],
            "boundary": "解释功能和提供建议，不自动批准、配置、导入或修改业务；无法保证覆盖未记录的实现细节。"}


def search_manual(query: str, limit: int = 2) -> list[dict]:
    tokens = set(lexemes(query).split())
    hits: list[dict] = []
    for key, title, keywords, implementation, body in ARTICLES:
        score = len(tokens & set(lexemes(title + " " + keywords).split()))
        if score < 2:
            continue
        content = f"{title}\n{body}\n核对实现：{implementation}。知识版本：{VERSION}。"
        hits.append({"id": f"platform:{key}:{hashlib.sha256(content.encode()).hexdigest()[:12]}", "title": title,
                     "text": content, "source": "平台内置手册 · " + VERSION, "kind": "platform_manual", "score": score})
    return sorted(hits, key=lambda item: (-item['score'], item['id']))[:limit]


def api_reference(schema: dict, query: str) -> list[dict]:
    """Derive field constraints from this running app's public OpenAPI, not prose guesses."""
    if not any(term in query.lower() for term in ('api', '接口', '参数', '必填字段', '字段限制', '请求格式')):
        return []
    aliases = {'project': '项目 立项 创建', 'chat': '对话 聊天 记忆', 'import': '导入 成员 csv',
               'knowledge': '知识 文档', 'auth': '登录 注册 账号 密码', 'approval': '审批 决策',
               'run': '执行 恢复 取消', 'company': '公司 成员', 'platform': '平台 后台'}
    terms = set(lexemes(query).split())
    components = schema.get('components', {}).get('schemas', {})

    def safe_fields(value, depth=0):
        if depth > 3:
            return {'detail': '嵌套结构请查看 /docs'}
        if '$ref' in value:
            return safe_fields(components.get(value['$ref'].split('/')[-1], {}), depth + 1)
        safe = {k: value[k] for k in ('type', 'format', 'enum', 'minimum', 'maximum', 'minLength', 'maxLength', 'pattern', 'required') if k in value}
        if 'properties' in value:
            safe['properties'] = {k: safe_fields(v, depth + 1) for k, v in list(value['properties'].items())[:30]}
        for key in ('anyOf', 'oneOf', 'allOf'):
            if key in value:
                safe[key] = [safe_fields(v, depth + 1) for v in value[key][:4]]
        if 'items' in value:
            safe['items'] = safe_fields(value['items'], depth + 1)
        return safe

    routes = []
    for path, methods in schema.get('paths', {}).items():
        if not path.startswith('/api/'):
            continue
        hints = ' '.join(words for key, words in aliases.items() if key in path)
        for method, operation in methods.items():
            if method not in {'get', 'post', 'put', 'patch', 'delete'}:
                continue
            score = len(terms & set(lexemes(path + ' ' + hints + ' ' + operation.get('summary', '')).split()))
            if path.lower() in query.lower():
                score += 50
            if method == 'post' and any(t in query for t in ('创建', '提交', '注册', '登录')):
                score += 2
            if score < 2:
                continue
            record = {'method': method.upper(), 'path': path, 'parameters': [{'name': p['name'], 'in': p['in'], 'required': p.get('required', False), 'schema': safe_fields(p.get('schema', {}))} for p in operation.get('parameters', [])],
                      'body': {mime: safe_fields(value.get('schema', {})) for mime, value in operation.get('requestBody', {}).get('content', {}).items()}}
            routes.append((score, path, method, record))
    selected = [entry[3] for entry in sorted(routes, key=lambda entry: (-entry[0], entry[1], entry[2]))[:2]]
    if not selected:
        return []
    text = '当前运行版本自动生成的接口约束；仅说明契约，不授予访问权限。未展示的约束请查看 /docs，不能推断没有约束。\n' + json.dumps(selected, ensure_ascii=False, separators=(',', ':'))
    return [{'id': 'api:' + hashlib.sha256(text.encode()).hexdigest()[:12], 'title': '当前版本接口与字段规范', 'text': text,
             'source': '运行中应用 OpenAPI · /docs', 'kind': 'platform_manual'}]
