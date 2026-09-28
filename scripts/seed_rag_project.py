"""Add one fictional RAG project through the running local API, without resetting data."""
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data/demo_workspace/XL-107'
NAME = 'XL-107｜售后工单与备件协同平台'
NOTICE = '> 虚构测试资料｜项目 XL-107｜版本 1.0｜2026-09-14｜项目内部资料。以下为拟建系统的需求设定，不代表当前 SaaS 平台已实现这些能力。\n\n'
DOCUMENTS = [
('01_项目立项与范围', '项目立项与范围', '''## 项目背景
星澜智造售后团队目前通过表格登记设备报修，通过群聊协调备件，重复录单和交接遗漏频发。XL-107 计划建立统一工单入口、备件申请记录和处理时效看板。项目代号为“青鹭”，业务负责人为周亦宁，实施负责人为陆远舟。

## 首期范围
首期服务 96 名内部用户：客服部 24 人、现场服务部 48 人、备件仓储部 16 人、质量管理部 8 人。试点仅覆盖苏州服务站和宁波服务站，共 320 台虚构设备。首期包含工单受理、分派、维修记录、备件申请、客户确认和结案。

## 范围边界
不包含客户自助门户、设备远程控制、付款、真实库存扣减、采购下单和短信通知。备件数量是夜间导入的参考快照，实际出库仍由仓库人员在原系统处理。项目成功不代表完成财务或 ERP 集成。

## 目标与里程碑
2026-10-12 启动，2026-10-23 冻结需求，2026-11-06 完成联调，2026-11-16 至 11-20 进行 UAT，2026-11-23 试点上线。试点两周后评估扩站。目标为受理漏登率低于 1%，工单必填字段完整率达到 98%。
'''),
('02_需求调研与业务设计', '需求调研与业务设计', '''## 现状与痛点
客服会重复登记同一设备故障；现场工程师在群聊中提交维修结果，质检人员难以追溯。仓库参考数量更新滞后，业务人员容易把可查数量当作可承诺库存。需求设计要求保留来源和更新时间。

## 目标业务过程
客服按设备编码、故障发生时间和联系方式建立工单。系统检查 24 小时内同一设备是否存在未结案工单，发现疑似重复时提示客服选择关联或继续创建，并记录原因。疑似重复不得自动删除。

## 分派与维修
客服主管按服务站和技能标签分派现场工程师。工程师接受后填写预约时间，到场后登记故障现象、处理动作和维修结果。缺少备件时发起备件申请并转等待备件；恢复维修需填写到货确认时间。

## 待确认事项
跨服务站借件由人工协调，一期只记录借件备注，不自动调拨。历史工单附件不迁移，只保留旧系统编号。客户满意度评分暂缓至二期，首期客户确认由客服登记电话确认结果。

## 需求优先级
P0 为工单闭环、项目权限隔离、审计和 SLA；P1 为备件申请与看板；P2 为满意度和客户门户，首期不交付 P2。权限越界问题是上线阻断项，不以需求优先级降低处理标准。
'''),
('03_PRD产品需求', 'PRD 产品需求', '''## 工单创建 FR-101
必填字段为设备编码、服务站、故障描述、优先级和联系人代号。故障描述为 20 至 2000 字。工单编号由服务端生成，格式为 AS-YYYYMMDD-六位流水号；编号在当前公司内唯一。提交成功后不得更改设备编码，填错时取消并新建关联工单。

## 工单状态 FR-102
状态依次为待受理、待分派、处理中、等待备件、待客户确认、已结案，另设已取消。处理中可以进入等待备件，到货确认后回到处理中；不能从等待备件直接结案。待客户确认可因客户拒绝回到处理中。

## 结案规则 FR-103
结案必须具备故障分类、维修结果、客户确认记录和实际工时。客户在 48 小时内没有回复只触发客服提醒，绝不自动结案。已结案工单不能直接编辑，7 个自然日内发现同一故障可由客服主管重开，超过 7 日新建关联工单。

## 备件申请 FR-104
每个申请最多 20 行备件，每行数量为 1 至 99 的整数。紧急申请必须填写至少 10 字原因。申请批准仅表示允许人工核查出库，不扣减真实库存、不调用采购接口。

## 搜索与附件 FR-105
支持按编号、设备编码、状态和服务站筛选。设计上每个工单最多 5 个附件，每个不超过 10 MB，仅允许 PDF、JPG、PNG；这是拟建工单系统需求，不是当前知识库上传限制。
'''),
('04_角色权限与私有资料', '角色权限与私有资料', '''## 权限原则
XL-107 文档属于项目私有知识库。只有当前公司内获得项目访问权的成员可检索，其他项目成员不能因同属公司而获得该项目资料。公司管理员按现有平台权限管理项目；本文件不授权用户越权。

## 拟建工单系统角色
客服专员可新建工单、查看所属服务站记录、登记客户确认，不可改写维修结论。客服主管可分派、取消、重开本服务站工单。现场工程师只能更新分派给自己的工单维修记录；备件管理员可处理本服务站备件申请，不得结案工单。质量专员可读取两站脱敏记录和质量看板，不得执行分派。

## 审批与脱敏
紧急备件申请由服务站主管审批，申请人不得审批本人申请。联系人在测试环境仅使用代号 C001 等，不写入真实姓名、电话或地址。导出数据必须删除联系方式，只保留联系人代号。

## 审计
分派、优先级调整、审批、重开、取消和导出必须记录操作者、时间、对象、动作和变更前后值。项目设定审计保留 180 天。权限校验失败返回统一禁止访问提示，不泄露其他站工单标题。
'''),
('05_数据模型与字段字典', '数据模型与字段字典', '''## 工单 Ticket
ticket_id 为 UUID 主键，ticket_no 为公司内唯一业务编号，device_code 为设备编码，station_code 只能为 SZ 或 NB。priority 允许 P1、P2、P3；status 使用 PRD 状态枚举；assignee_id 可在待分派阶段为空。contact_alias 存放虚构联系人代号。

## 维修与时效字段
accepted_at 记录受理时间，assigned_at 记录首次分派时间，resolved_at 记录提交维修结果时间，closed_at 记录客户确认后结案时间。所有接口传递带时区的 ISO 8601 时间，页面按 Asia/Shanghai 显示。work_minutes 为非负整数，不允许用字符串“半天”替代。

## 备件与申请
PartSnapshot 包含 part_code、part_name、station_code、reference_quantity 和 snapshot_at。reference_quantity 为非负整数，仅供参考。PartRequest 包含 request_id、ticket_id、reason、approval_status；明细 PartRequestLine 包含 part_code 和 requested_quantity。不能通过申请审批修改 PartSnapshot。

## 编码与校验
设备编码示例为 XL-SZ-0001，备件编码示例为 PT-047。工单关联的设备必须属于选定服务站；不存在的设备返回 DEVICE_NOT_FOUND，服务站不符返回 DEVICE_STATION_MISMATCH。备注最长 1000 字，空白备注存为 null，不以“无”替代缺失值。
'''),
('06_接口与集成设计', '接口与集成设计', '''## 接口约定
以下是拟建售后系统接口设计，不是当前 SaaS Agent 的已发布接口。路径前缀为 /aftercare/v1。身份验证采用企业网关颁发的 Bearer Token，服务端按公司、服务站和角色校验资源，不信任客户端传入的角色字段。

## 创建与查询
POST /aftercare/v1/tickets 创建工单，必填字段遵循 FR-101。创建请求携带 Idempotency-Key，同一调用方同一键 24 小时内重复提交相同内容返回原工单，不创建重复项；相同键不同内容返回 409 IDEMPOTENCY_CONFLICT。GET /aftercare/v1/tickets 支持游标分页，默认 20 条，最大 100 条。

## 维修与备件
PATCH /aftercare/v1/tickets/{id}/resolution 提交维修结果，使用 version 乐观锁，版本冲突返回 409 VERSION_CONFLICT。POST /aftercare/v1/part-requests 创建备件申请。首期不提供自动出库、采购或付款接口。

## 快照与失败处理
仓库每晚 02:00 导出 CSV，集成作业 02:15 导入参考库存快照。失败后每 10 分钟重试一次，最多 3 次；仍失败时保留上一成功快照并在页面显示“快照过期”，通知备件管理员。日志包含 trace_id，禁止记录 Token 原文。
'''),
('07_数据迁移与导入方案', '数据迁移与导入方案', '''## 迁移范围
迁移 96 名内部用户、320 台设备基础信息和最近 90 天的 600 条虚构历史工单。旧系统附件、真实联系人信息和已停用设备不迁移。600 条历史工单中 540 条已结案、60 条处理中；处理中工单必须映射有效工程师。

## 文件与映射
文件采用 UTF-8 CSV。设备表必填 device_code、station_code、model_name；工单表必填 legacy_id、device_code、status、priority、contact_alias。legacy_id 是去重键，重复执行同一迁移批次不得产生新工单。旧状态“维修中”映射为处理中，“待料”映射为等待备件。

## 校验与发布
先在暂存区校验，输出文件名、行号、错误码和字段。重复设备编码、未知服务站、缺失 legacy_id 均使批次整体拒绝，不允许部分写入正式表。未知优先级也应拒绝，不能默认为 P3。通过后由质量专员复核数量，实施负责人提交导入，业务负责人确认发布。

## 对账和回退
成功标准为设备 320 条、历史工单 600 条且孤立关联为 0，按站点和状态分组核对。每批记录 migration_batch_id 和文件 SHA-256。发布后 24 小时内发现差异可申请按批次回退，但已新增维修记录的工单须人工处理，不能直接删除。
'''),
('08_SLA与通知规则', 'SLA 与通知规则', '''## 优先级定义
P1 表示设备停机且现场无替代方案；P2 表示功能受损但有替代方案；P3 表示咨询或一般维护。客服可以建议优先级，客服主管负责调整并填写原因，系统保留调整前后值。

## 首次响应时限
P1 首次响应为 15 分钟，P2 为 2 个工作小时，P3 为 8 个工作小时。P1 按 7×24 小时连续计时；P2 和 P3 只在周一至周五 09:00—18:00 计时，本测试日历不配置节假日，中午不暂停。首次响应定义为客服受理，不是工程师到场。

## 维修目标与暂停
P1 修复目标为 4 小时，P2 为 16 个工作小时，P3 为 40 个工作小时。等待备件只暂停修复时钟，不暂停首次响应时钟；只有填写缺料原因、关联备件申请后才允许暂停。恢复处理中时继续累计，不能清零。

## 通知与升级
达到时限 80% 时向责任人发送一次站内提醒；超时立即通知服务站主管，每 60 分钟最多追加一次。首期仅站内通知，没有短信或企业微信推送。因优先级调整导致立即超时，应记录调整事件并触发通知，不追溯发送历史提醒。
'''),
('09_测试计划与UAT验收', '测试计划与 UAT 验收', '''## 验收时间与责任
UAT 为 2026-11-16 至 11-20，业务负责人周亦宁签署验收，实施人员不能代签。测试覆盖苏州和宁波两站，使用虚构设备、联系人代号与专用账号，禁止复制真实客户信息。

## 核心场景
TC-01：完整走通创建、分派、维修、客户确认、结案。TC-02：等待备件不能直接结案，恢复后时钟继续累计。TC-03：客户 48 小时未回复只提醒，不自动结案。TC-04：重复幂等键返回同一工单。TC-05：工程师读取未分配工单与跨站查询均被拒绝。

## 性能与质量门槛
100 个并发用户下，工单列表查询 P95 不超过 1.5 秒，创建工单 P95 不超过 2 秒。计划 60 条验收用例，至少 57 条通过；所有 P0 用例必须通过，未关闭的阻断级与严重级缺陷数量必须为 0。即使总通过率达标，权限越界也必须阻止上线。

## 迁移与知识查验
设备数为 320、历史工单数为 600；幂等重放后数量不变。知识检索应能区分首次响应和修复时限，能找到不自动结案的原文。文档没有提供项目采购预算、供应商报价和生产密钥，不应从现有片段推断这些信息。
'''),
('10_上线运维与回滚预案', '上线运维与回滚预案', '''## 上线窗口
拟于 2026-11-23 20:00—22:00 完成试点切换。19:30 停止旧表格录入，20:00 执行最终增量导入与对账，21:00 由业务负责人确认切换，22:00 结束观察。未获得 UAT 签署不得开始切换。

## 回滚触发与责任
任意跨站数据泄露立即停止服务并启动回滚；核心创建接口错误率连续 10 分钟超过 5%，或最终对账差异无法在 30 分钟内清除，也触发回滚。实施负责人陆远舟执行，业务负责人周亦宁确认，值班人员记录事件时间线。

## 回滚步骤
暂停新建入口并导出切换后新增工单；恢复切换前数据库快照；验证登录、权限和工单数量；重新开放旧表格受理；人工补录导出的新增工单并核对去重键。不得丢弃已受理报修。设计恢复目标 RTO 为 30 分钟，RPO 为 15 分钟，须演练后再确认可达成。

## 运维和后续
上线首周每天 09:30、16:30 检查错误率、SLA 超时、参考库存快照和权限审计。故障演练至少覆盖快照导入失败、幂等冲突和跨站访问。项目采购预算尚未制定，供应商报价及生产密钥不在本测试资料集内。
'''),
]


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fixture = json.loads((ROOT / 'data/demo_workspace/manifest.json').read_text(encoding='utf-8'))
    credentials = json.loads((ROOT / 'data/demo_workspace/credentials.json').read_text(encoding='utf-8'))
    for filename, title, body in DOCUMENTS:
        (OUTPUT / f'{filename}.md').write_text(f'# XL-107 {title}\n\n{NOTICE}{body}', encoding='utf-8')
    manifest_path = OUTPUT / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'documents': []}
    with httpx.Client(base_url='http://127.0.0.1:8000', trust_env=False, timeout=120) as client:
        def call(method, path, payload=None):
            response = client.request(method, path, json=payload, headers={
                'X-CSRF-Token': client.cookies.get('saas_csrf', ''), 'Idempotency-Key': str(uuid4())})
            if response.status_code >= 400:
                raise RuntimeError(f'{method} {path}: HTTP {response.status_code}')
            return response.json().get('data', response.json())
        def save():
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        account = credentials['admin']
        call('POST', '/api/auth/login', {'username': account['username'], 'password': account['password']})
        call('POST', f"/api/auth/spaces/{fixture['tenant_id']}/switch")
        projects = call('GET', '/api/projects')
        project = next((p for p in projects if p['name'] == NAME), None)
        if project is None:
            from scripts.demo_documents import project_payload
            payload = project_payload(dict(code='XL-107', name='售后工单与备件协同平台', contact='周亦宁', count=96,
                date='2026-11-23', departments=['客服部', '现场服务部', '备件仓储部', '质量管理部'],
                objective='建立售后工单闭环和项目私有知识库，专用于 RAG 检索查验',
                specific='试点苏州和宁波两站，320 台设备，工单分派、维修、备件申请、客户确认和结案。首期不含付款、采购、库存扣减和客户门户。',
                metric='60 条 UAT 用例至少 57 条通过，全部 P0 用例通过，无阻断或严重缺陷',
                risk='全部资料为虚构需求设定，不代表系统已有能力', stop='draft'))
            project = call('POST', '/api/projects', payload)
        pid = project['id']
        manifest.update(project_id=pid, project_name=NAME, tenant_id=fixture['tenant_id'])
        save()
        members = {m['user_id'] for m in call('GET', f'/api/projects/{pid}/members')}
        for role in ('consultant', 'approver', 'viewer'):
            uid = fixture['users'][role]
            if uid not in members:
                call('POST', f'/api/projects/{pid}/members', {'user_id': uid, 'primary_role_code': credentials[role]['role']})
        existing = call('GET', '/api/knowledge')
        for filename, title, _ in DOCUMENTS:
            title = f'XL-107 {title}'
            file = OUTPUT / f'{filename}.md'
            document = next((d for d in existing if d['title'] == title and d['project_id'] == pid), None)
            if document is None:
                document = call('POST', '/api/knowledge', dict(project_id=pid, title=title, version=1,
                    module='aftercare', source=f'XL-107/{file.name}', license='虚构测试资料，仅项目授权成员使用', body=file.read_text(encoding='utf-8')))
            if not any(d['id'] == document['id'] for d in manifest['documents']):
                manifest['documents'].append(dict(id=document['id'], title=title, file=str(file)))
            save()
            print(f"Registered {filename}", flush=True)
        ids = {d['id'] for d in manifest['documents']}
        for _attempt in range(90):
            rows = [d for d in call('GET', '/api/knowledge') if d['id'] in ids]
            ready = sum(d['index_status'] == 'ready' for d in rows)
            failed = sum(d['index_status'] == 'failed' for d in rows)
            print(f'Index ready={ready}/10 failed={failed}', flush=True)
            manifest['index_status'] = {d['id']: d['index_status'] for d in rows}
            save()
            if ready == 10:
                break
            if failed:
                raise RuntimeError('Index failed; inspect project document index status')
            time.sleep(5)
        else:
            raise RuntimeError('Index still pending after timeout')
        print('Project and ten indexed documents ready.', flush=True)


if __name__ == '__main__':
    main()
