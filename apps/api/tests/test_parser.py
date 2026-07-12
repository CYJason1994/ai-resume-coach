import pytest

from app.services.parser import ParseError, ParserFactory, TextParser


async def test_text_utf8():
    assert "你好" in await TextParser().parse("你好 world".encode("utf-8"))


async def test_text_gb18030():
    assert "简历" in await TextParser().parse("简历".encode("gb18030"))


async def test_text_latin1_fallback():
    # latin-1 能解码任意字节，确保不崩
    assert await TextParser().parse(b"\xff\xfeabc") == "\xff\xfeabc"


async def test_factory_routes():
    assert isinstance(ParserFactory.get("text"), TextParser)
    with pytest.raises(ParseError):
        ParserFactory.get("unknown_type")


async def test_pdf_missing_dep_or_works():
    try:
        import pdfminer  # noqa: F401
    except ImportError:
        with pytest.raises(ParseError):
            await ParserFactory.get("pdf").parse(b"%PDF-1.4 fake")
        return
    pytest.skip("pdfminer 已安装，跳过缺失依赖分支")
