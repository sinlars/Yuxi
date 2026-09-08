"""ZIP 导入图片链接替换与图片目录发现的单元测试。"""

import io
import zipfile

from yuxi.knowledge.parser.zip_utils import (
    find_images_directory,
    replace_image_links,
)

PROXY_URL = "/api/knowledge/databases/kb_1/images/kb-images/1_a.png"


def _images() -> list[dict]:
    return [{"name": "a.png", "url": PROXY_URL, "path": "images/a.png"}]


def _zip(entries: dict[str, bytes]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_replace_markdown_link_basic():
    md = "前文 ![alt](images/a.png) 后文"
    assert replace_image_links(md, _images()) == f"前文 ![alt]({PROXY_URL}) 后文"


def test_replace_markdown_link_with_typora_size_suffix():
    """Typora 尺寸后缀（如 " =413x"）不应阻断匹配，且替换后一并去除。"""
    md = "![图0.1 人类大脑示意图](images/a.png =413x)"
    assert replace_image_links(md, _images()) == f"![图0.1 人类大脑示意图]({PROXY_URL})"


def test_replace_markdown_link_with_typora_width_height_suffix():
    md = "![alt](images/a.png =413x300)"
    assert replace_image_links(md, _images()) == f"![alt]({PROXY_URL})"


def test_unmatched_link_kept_as_is():
    md = "![alt](images/missing.png)"
    assert replace_image_links(md, _images()) == md


def test_replace_html_img_tag():
    md = '前文 <img src="images/a.png" alt="x"> 后文'
    assert replace_image_links(md, _images()) == f'前文 <img src="{PROXY_URL}" alt="x"> 后文'


def test_find_images_directory_supports_common_names():
    with _zip({"doc.md": b"x", "img/a.png": b"p"}) as zf:
        assert find_images_directory(zf, "doc.md") == "img"


def test_find_images_directory_none_when_absent():
    with _zip({"doc.md": b"x"}) as zf:
        assert find_images_directory(zf, "doc.md") is None
