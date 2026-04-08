"""app/llm.py の _extract_json_list ユニットテスト"""

import pytest
from app.llm import _extract_json_list


class TestExtractJsonList:
    def test_正常なJSONオブジェクトをリストで返す(self):
        text = '{"商品名": "キャベツ", "価格": "380"}'
        result = _extract_json_list(text)
        assert result == [{"商品名": "キャベツ", "価格": "380"}]

    def test_正常なJSON配列をそのまま返す(self):
        text = '[{"商品名": "キャベツ"}, {"商品名": "豆腐"}]'
        result = _extract_json_list(text)
        assert result == [{"商品名": "キャベツ"}, {"商品名": "豆腐"}]

    def test_前後に空白があってもパースできる(self):
        text = '  {"商品名": "キャベツ"}  '
        result = _extract_json_list(text)
        assert result == [{"商品名": "キャベツ"}]

    def test_マークダウンコードブロック内のJSONを抽出できる(self):
        text = '```json\n{"商品名": "キャベツ", "価格": "380"}\n```'
        result = _extract_json_list(text)
        assert result == [{"商品名": "キャベツ", "価格": "380"}]

    def test_説明文の後のJSONを抽出できる(self):
        text = '以下がJSON出力です。\n{"商品名": "キャベツ"}'
        result = _extract_json_list(text)
        assert result == [{"商品名": "キャベツ"}]

    def test_複数行にわたるJSONを抽出できる(self):
        text = """以下のとおりです。
{
  "商品名": "キャベツ",
  "価格": "380"
}
ご確認ください。"""
        result = _extract_json_list(text)
        assert result == [{"商品名": "キャベツ", "価格": "380"}]

    def test_nullを含むJSONをパースできる(self):
        text = '{"商品名": "キャベツ", "JANコード": null}'
        result = _extract_json_list(text)
        assert result == [{"商品名": "キャベツ", "JANコード": None}]

    def test_説明文の後のJSON配列を抽出できる(self):
        text = '抽出結果:\n[{"品名": "A", "金額": "100"}, {"品名": "B", "金額": "200"}]'
        result = _extract_json_list(text)
        assert result == [{"品名": "A", "金額": "100"}, {"品名": "B", "金額": "200"}]

    def test_完全に不正なテキストはNoneを返す(self):
        text = "これはJSONではありません"
        result = _extract_json_list(text)
        assert result is None

    def test_空文字はNoneを返す(self):
        result = _extract_json_list("")
        assert result is None

    def test_不完全なJSONはNoneを返す(self):
        text = '{"商品名": "キャベツ"'
        result = _extract_json_list(text)
        assert result is None
