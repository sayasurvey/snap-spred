"""app/llm.py の _extract_json ユニットテスト"""

import pytest
from app.llm import _extract_json


class TestExtractJson:
    def test_正常なJSONをパースできる(self):
        text = '{"商品名": "キャベツ", "価格": "380"}'
        result = _extract_json(text)
        assert result == {"商品名": "キャベツ", "価格": "380"}

    def test_前後に空白があってもパースできる(self):
        text = '  {"商品名": "キャベツ"}  '
        result = _extract_json(text)
        assert result == {"商品名": "キャベツ"}

    def test_マークダウンコードブロック内のJSONを抽出できる(self):
        text = '```json\n{"商品名": "キャベツ", "価格": "380"}\n```'
        result = _extract_json(text)
        assert result == {"商品名": "キャベツ", "価格": "380"}

    def test_説明文の後のJSONを抽出できる(self):
        text = '以下がJSON出力です。\n{"商品名": "キャベツ"}'
        result = _extract_json(text)
        assert result == {"商品名": "キャベツ"}

    def test_複数行にわたるJSONを抽出できる(self):
        text = """以下のとおりです。
{
  "商品名": "キャベツ",
  "価格": "380"
}
ご確認ください。"""
        result = _extract_json(text)
        assert result == {"商品名": "キャベツ", "価格": "380"}

    def test_nullを含むJSONをパースできる(self):
        text = '{"商品名": "キャベツ", "JANコード": null}'
        result = _extract_json(text)
        assert result == {"商品名": "キャベツ", "JANコード": None}

    def test_完全に不正なテキストはNoneを返す(self):
        text = "これはJSONではありません"
        result = _extract_json(text)
        assert result is None

    def test_空文字はNoneを返す(self):
        result = _extract_json("")
        assert result is None

    def test_不完全なJSONはNoneを返す(self):
        text = '{"商品名": "キャベツ"'
        result = _extract_json(text)
        assert result is None
