"""Exercise scanned PDF OCR, V3 indexing, and page-correct evidence in local Docker."""

import io
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from verify_index_lifecycle import cleanup

ROOT = Path(__file__).resolve().parents[1]


def scanned_pdf():
    image = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("arial.ttf", 65)
    draw.text((100, 150), "The audit code is CORAL 72941.", font=font, fill="black")
    draw.text((100, 270), "Keep this code for the project review.", font=font, fill="black")
    chinese = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 65)
    draw.text((100, 390), "项目验收编号 72941", font=chinese, fill="black")
    output = io.BytesIO()
    image.save(output, format="PDF", resolution=150)
    return output.getvalue()


def text_with_blank_pages():
    writer = PdfWriter()
    source = ROOT / "evaluations" / "v3" / "fixtures" / "inventory.pdf"
    writer.add_page(PdfReader(source).pages[0])
    for _ in range(31):
        writer.add_blank_page(width=612, height=792)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def main():
    workspace = ROOT / "data" / "demo_workspace"
    admin = json.loads((workspace / "credentials.json").read_text(encoding="utf-8"))["admin"]
    manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))
    title = f"索引验收临时资料-{uuid4().hex[:12]}"
    uploaded = []
    with httpx.Client(base_url="http://127.0.0.1:8080", trust_env=False, timeout=45) as client:
        def post(path, payload):
            response = client.post(path, json=payload,
                headers={"X-CSRF-Token": client.cookies.get("saas_csrf", "")})
            response.raise_for_status()
            return response.json()["data"]

        post("/api/auth/login", {"username": admin["username"], "password": admin["password"]})
        post(f"/api/auth/spaces/{manifest['tenant_id']}/switch", {})
        try:
            response = client.post("/api/knowledge/files",
                data={"title": title, "version": "1", "module": "PDF 验收", "license": "本机验收"},
                files={"file": ("scan.pdf", scanned_pdf(), "application/pdf")},
                headers={"X-CSRF-Token": client.cookies.get("saas_csrf", "")})
            response.raise_for_status()
            document_id = response.json()["data"]["id"]
            uploaded.append((document_id, title))
            for _ in range(90):
                detail = client.get(f"/api/knowledge/{document_id}").json()["data"]
                if detail["v3"]["phase"] in {"ready", "failed"}:
                    break
                time.sleep(3)
            assert detail["v3"]["phase"] == "ready", detail["v3"]
            assert "CORAL" in detail["body"] and "72941" in detail["body"], detail["body"]
            assert "项目" in detail["body"], detail["body"]
            assert "## 第 1 页" in detail["body"]
            result = post("/api/knowledge/inspect", {"question": "What is the audit code CORAL 72941?"})
            matching = [chunk for chunk in result["chunks"] if chunk["origin_id"] == document_id]
            assert matching and all(chunk["location"]["page"] == 1 for chunk in matching), result
            assert "CORAL" in result["evidence_text"] and "72941" in result["evidence_text"]
            print(json.dumps({"pdf_ocr": "passed", "v3": "passed", "page": 1,
                              "warnings": detail["v3"]["warnings"]}, ensure_ascii=False))
            blank_title = f"空白页验收临时资料-{uuid4().hex[:12]}"
            response = client.post("/api/knowledge/files",
                data={"title": blank_title, "version": "1", "module": "PDF 验收", "license": "本机验收"},
                files={"file": ("blank-pages.pdf", text_with_blank_pages(), "application/pdf")},
                headers={"X-CSRF-Token": client.cookies.get("saas_csrf", "")})
            response.raise_for_status()
            blank_id = response.json()["data"]["id"]
            uploaded.append((blank_id, blank_title))
            for _ in range(90):
                blank = client.get(f"/api/knowledge/{blank_id}").json()["data"]
                if blank["v3"]["phase"] in {"ready", "failed"}:
                    break
                time.sleep(3)
            assert blank["v3"]["phase"] == "ready", blank["v3"]
            assert blank["v3"]["metrics"]["ocr_pages"] == 0, blank["v3"]
            print(json.dumps({"pdf_blank_pages": "passed", "ocr_pages": 0}))
        finally:
            for document_id, uploaded_title in uploaded:
                cleanup(document_id, uploaded_title)


if __name__ == "__main__":
    main()
