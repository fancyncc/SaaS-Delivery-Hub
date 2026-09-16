import io
import json
import zipfile
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from backend.rag_v3_parse import parse, Node
from backend.rag_v3 import needs_retrieval, assemble, serialize, visible_documents
from backend.rag_v3_index import split_node, register
from backend.rag_v3_models import V3Document
from backend.models import KnowledgeDocument, Tenant
from backend.db import SessionLocal


def test_formats_and_locations():
    csv=parse('x.csv',b'id,description\nA,"a,b\nsecond line"\n')
    assert csv.nodes[1].text=='a,b\nsecond line'
    assert csv.nodes[1].record==csv.nodes[0].record
    assert csv.nodes[1].context=='description'
    data=parse('x.json',b'{"a":[{"enabled":false,"limit":0}]}')
    assert data.nodes[-1].context=='$["a"][0]["limit"]'
    assert data.nodes[-2].text=='false'
    md=parse('x.md','# 标题\n\n| 字段 | 值 |\n|---|---|\n| x | 不得超过 5 |\n\n```py\na = 1\n```'.encode())
    cells=[n for n in md.nodes if n.kind=='table_cell']
    assert len(cells)==2 and cells[1].context=='值' and cells[1].text=='不得超过 5'
    assert cells[0].record==cells[1].record and cells[0].heading=='标题'
    assert any(n.kind=='code' and 'a = 1' in n.text for n in md.nodes)
    txt=parse('x.txt','第一段。\n\n第二段不得删除。'.encode())
    assert txt.nodes[1].location['chars']==[6,14]


def test_docx_order_and_warning():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('word/document.xml','''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Policy</w:t></w:r></w:p>
        <w:tbl><w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc><w:p><w:r><w:t>Limit</w:t></w:r></w:p></w:tc></w:tr>
        <w:tr><w:tc><w:p><w:r><w:t>12</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:p><w:r><w:t>Not allowed</w:t><w:drawing/></w:r></w:p></w:body></w:document>''')
    result=parse('x.docx',stream.getvalue())
    assert [n.kind for n in result.nodes]==['heading','table_cell','paragraph']
    assert result.nodes[1].context=='Limit' and result.nodes[1].location=={'block':1,'row':2,'column':1}
    assert result.warnings


@pytest.mark.parametrize('name,raw,status', [('x.pdf',b'%PDF',415),('x.doc',b'x',415),('x.txt',b'\xff',422),
    ('x.docx',b'broken',422),('x.json',b'{"a":1,"a":2}',422),('x.json',b'{"a":NaN}',422),('x.csv',b'a,b\n"broken',422)])
def test_rejections(name,raw,status):
    with pytest.raises(HTTPException) as e: parse(name,raw)
    assert e.value.status_code==status


@pytest.mark.parametrize('q',['什么是客户内部规则','What is E_403?','Translate the internal policy','你好，项目什么时候上线？','¿Cuál es el límite?'])
def test_unknown_inputs_retrieve(q):
    assert needs_retrieval(q)
    assert not needs_retrieval('你好！')


async def fake_counts(texts): return [len(t) for t in texts]


async def test_long_atomic_text_not_rewritten(monkeypatch):
    monkeypatch.setattr('backend.rag_v3_index.model_counts',fake_counts)
    node=Node('0','code','x'*900,{'lines':[1,1]})
    parts=await split_node(node)
    assert ''.join(parts)==node.text and max(map(len,parts))<=384
    assert node.location['requires_all_parts']


def evidence(i,text,**kw):
    return dict(id=str(i),document_id='d',title='Policy',version=1,heading='',kind='paragraph',location={},
        text=text,ordinal=i,source_chunk_ids=[str(i)],**kw)


async def test_budget_and_duplicates_preserve_negation(monkeypatch):
    monkeypatch.setattr('backend.rag_v3.token_counts',fake_counts)
    data=[evidence(0,'Do not exceed 12.'),evidence(1,'Do not exceed 12.'),evidence(2,'Z'*590)]
    chosen,text,n,saved,omitted,_=await assemble(data,600)
    assert len(chosen)==1 and n==len(text)<=600
    assert 'Do not exceed 12.' in text and saved>0 and omitted==['2']
    assert chosen[0]['source_chunk_ids']==['0'] and data[1]['duplicate_of']=='0'
    assert text=='\n\n'.join(serialize(h) for h in chosen)


async def test_conflict_keeps_sources(monkeypatch):
    monkeypatch.setattr('backend.rag_v3.token_counts',fake_counts)
    a=evidence(0,'12'); b=evidence(1,'13'); b['document_id']='e'
    a['location']=b['location']={'path':'$["limit"]'}
    selected,_,_,_,_,conflicts=await assemble([a,b],600)
    assert len(selected)==2 and conflicts==[['0','1']]


async def test_file_upload_and_closed_gate(client):
    fields={'title':'CSV data','version':'1','module':'testing','license':'Internal'}
    r=await client.post('/api/knowledge/files',data=fields,files={'file':('data.csv',b'id,value\nx,12','text/csv')})
    assert r.status_code==200, r.text
    identifier=r.json()['data']['id']
    detail=(await client.get('/api/knowledge/'+identifier)).json()['data']
    assert detail['v3']['phase']=='pending' and detail['index_status']=='v3_pending'
    r=await client.post('/api/knowledge/files',data={**fields,'version':'2'},files={'file':('a.pdf',b'%PDF')})
    assert r.status_code==415
    r=await client.post('/api/knowledge/inspect',json={'question':'policy','pipeline':'v3'})
    assert r.status_code==503 and 'V3' in r.text


async def test_new_pending_version_hides_old_and_tenant_isolation():
    async with SessionLocal() as session:
        tenant=await session.scalar(select(Tenant).where(Tenant.slug=='legacy-demo'))
        docs=[]
        for version in (1,2):
            doc=KnowledgeDocument(tenant_id=tenant.id,title='Versioned',version=version,module='test',source='original',license='test',body='content')
            session.add(doc); await session.flush(); source=await register(session,doc); docs.append(source)
        docs[0].phase='ready'
        await session.flush()
        visible=(await session.execute(visible_documents(tenant.id,[]))).all()
        assert len(visible)==1 and visible[0][0].id==docs[1].id
        assert not (await session.execute(visible_documents(str(uuid4()),[]))).all()
