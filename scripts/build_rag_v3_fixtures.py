"""Synthetic boundary fixtures, explicitly NOT independently human-labelled evaluation."""
import csv
import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT=Path(__file__).resolve().parents[1]/'evaluations/v3'
DOMAINS=[
    ('inventory','库存调拨','stock transfer','SKU',17,'调拨前必须确认库存；冻结货品不得调拨。','Confirm stock before transfer; frozen items must not be transferred.'),
    ('education','课程选修','course enrollment','COURSE',23,'退选必须在截止日期前；已结课课程不可退选。','Withdraw before the deadline; completed courses cannot be withdrawn.'),
    ('facilities','设备巡检','equipment inspection','ASSET',31,'巡检前必须停机；紧急停机不受排期限制。','Stop equipment before inspection; emergency shutdown is exempt from scheduling.'),
    ('publishing','稿件审阅','manuscript review','DRAFT',41,'发布前必须复核；撤回稿件不得发布。','Review before publication; withdrawn manuscripts must not be published.'),
    ('events','活动报名','event registration','EVENT',53,'报名需确认席位；候补不等于报名成功。','Confirm a seat before registration; waitlisting does not mean confirmed registration.'),
    ('software','构建任务','build job','BUILD',67,'重试必须使用原请求标识；取消的任务不得自动重试。','Reuse the original request identifier for retry; cancelled jobs must not retry automatically.'),
]


def write_docx(path,heading,records):
    def paragraph(text,style=''):
        return '<w:p>'+('<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if style else '')+'<w:r><w:t xml:space="preserve">'+escape(text)+'</w:t></w:r></w:p>'
    body=paragraph(heading,'heading')+''.join(paragraph(k+': '+v) for k,v in records.items())
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+body+'</w:body></w:document>')


def main():
    fixtures=ROOT/'fixtures'
    fixtures.mkdir(parents=True,exist_ok=True)
    cases=[]
    for d,(domain,zh,en,code,limit,rule_zh,rule_en) in enumerate(DOMAINS):
        for fmt in ['md','txt','csv','json','docx']:
            identifier=code+'-204'
            records={'id':identifier,'limit':str(limit),'conditions':rule_zh+' '+rule_en,
                'fields':'id, owner, created_at, status','distractor':'ARCHIVE-999 uses a separate limit of 999. 此限制不适用于当前对象。'}
            path=fixtures/(domain+'.'+fmt)
            if fmt=='json':
                path.write_text(json.dumps({identifier:records},ensure_ascii=False,indent=2),encoding='utf-8')
            elif fmt=='csv':
                stream=io.StringIO(newline='')
                writer=csv.writer(stream)
                writer.writerow(records.keys())
                writer.writerow(records.values())
                path.write_text(stream.getvalue(),encoding='utf-8')
            elif fmt=='docx':
                write_docx(path,zh+' / '+en,records)
            else:
                prefix='# ' if fmt=='md' else ''
                path.write_text(prefix+zh+' / '+en+'\n\n'+'\n\n'.join(k+': '+v for k,v in records.items()),encoding='utf-8')
            questions=[('fact','zh',f'{identifier} 的 limit 是多少？',[str(limit)],['limit']),
                ('enumeration','en',f'List all fields of {identifier}.',['id','owner','created_at','status'],['fields']),
                ('conditions','zh',f'{identifier} 的操作条件和例外是什么？',[rule_zh],['conditions']),
                ('noanswer','en',f'What is the insurance premium of {identifier}?',[],[])]
            for n,(kind,language,question,facts,keys) in enumerate(questions):
                cases.append({'id':f'{domain}-{fmt}-{n}','split':'calibration' if d<3 else 'validation',
                    'domain':domain,'document_group':domain,'format':fmt,'language':language,'kind':kind,
                    'question':question,'document':str(path.relative_to(ROOT)).replace('\\','/'),
                    'necessary_facts':facts,'source_keys':keys,'protected_conditions':[rule_zh,rule_en] if kind=='conditions' else [],
                    'distractors':['ARCHIVE-999','999'],'allow_no_answer':kind=='noanswer',
                    'provenance':'synthetic_rule_fixture','review_status':'pending_independent_review'})
    (ROOT/'cases.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases),encoding='utf-8')
    print(f'{len(cases)} synthetic cases; independent review pending')


if __name__=='__main__':
    main()
