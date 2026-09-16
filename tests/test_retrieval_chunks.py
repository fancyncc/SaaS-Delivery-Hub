import json

import httpx
import pytest
from fastapi import HTTPException
from test_retrieval_models import mock_http

from backend import retrieval_models as models
from backend.config import get_settings


async def test_http_chunks_use_authenticated_model_and_preserve_structure(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, 'embedding_base_url', 'https://embedding.example/v1')
    monkeypatch.setattr(s, 'embedding_model', 'test')
    monkeypatch.setattr(s, 'embedding_api_key', 'test-only')

    def handler(request):
        assert request.url.path == '/v1/chunks'
        assert request.headers['Authorization'] == 'Bearer test-only'
        assert json.loads(request.content) == {'model': 'test', 'text': '# 步骤\n正文', 'size': 220, 'overlap': 30}
        return httpx.Response(200, json={'model': 'test', 'revision': s.embedding_revision,
                                      'chunks': [{'heading': '步骤', 'text': '# 步骤\n正文'}]})

    mock_http(monkeypatch, handler)
    assert await models.chunks('# 步骤\n正文') == [('步骤', '# 步骤\n正文')]


@pytest.mark.parametrize('payload', [{}, {'model': 'wrong', 'revision': '', 'chunks': []},
                                    {'model': 'test', 'revision': 'wrong', 'chunks': []}])
async def test_http_chunks_fail_closed(monkeypatch, payload):
    s = get_settings()
    monkeypatch.setattr(s, 'embedding_base_url', 'https://embedding.example/v1')
    monkeypatch.setattr(s, 'embedding_model', 'test')
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(HTTPException) as error:
        await models.chunks('需要索引的资料')
    assert error.value.status_code == 502


def test_pdf_page_windows_never_cross_pages():
    from backend.chunking import chunk_text
    result = chunk_text('第 1 页\n第一页内容\n第 2 页\n# 操作\n第二页内容')
    assert len(result) == 2
    assert result[0] == ('第 1 页', '第一页内容')
    assert result[1][0] == '第 2 页 · 操作'
    assert '第一页' not in result[1][1]


def test_docx_table_preserves_cells():
    import io
    import zipfile

    from backend.chat import extract_document
    document = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>字段</w:t></w:r></w:p></w:tc>'
                '<w:tc><w:p><w:r><w:t>要求</w:t></w:r></w:p></w:tc></w:tr>'
                '<w:tr><w:tc><w:p><w:r><w:t>邮箱</w:t></w:r></w:p></w:tc>'
                '<w:tc><w:p><w:r><w:t>必填</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('word/document.xml', document)
    assert extract_document('test.docx', stream.getvalue()) == '| 字段 | 要求 |\n| --- | --- |\n| 邮箱 | 必填 |'
