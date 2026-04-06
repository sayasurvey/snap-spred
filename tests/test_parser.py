"""app/parser.py のユニットテスト"""

import pytest
from app.parser import parse_format, build_row


class TestParseFormat:
    def test_基本的なフォーマット解析(self):
        result = parse_format("A列: 商品名, B列: 価格, C列: 生産者")
        assert result["columns"] == {"A": "商品名", "B": "価格", "C": "生産者"}
        assert result["llm_fields"] == ["商品名", "価格", "生産者"]
        assert result["system_fields"] == {}

    def test_処理日はシステム補完される(self):
        result = parse_format("A列: 商品名, B列: 処理日")
        assert "B" in result["system_fields"]
        field_name, value = result["system_fields"]["B"]
        assert field_name == "処理日"
        # YYYY/MM/DD 形式かどうか確認
        import re
        assert re.match(r"\d{4}/\d{2}/\d{2}$", value)
        assert "処理日" not in result["llm_fields"]

    def test_登録日時はシステム補完される(self):
        result = parse_format("A列: 商品名, B列: 登録日時")
        assert "B" in result["system_fields"]
        field_name, value = result["system_fields"]["B"]
        assert field_name == "登録日時"
        import re
        assert re.match(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}$", value)

    def test_全角コロンでも解析できる(self):
        result = parse_format("A列：商品名, B列：価格")
        assert result["columns"] == {"A": "商品名", "B": "価格"}

    def test_列名が大文字小文字どちらでも動作する(self):
        result = parse_format("a列: 商品名, b列: 価格")
        assert "A" in result["columns"]
        assert "B" in result["columns"]

    def test_空文字でエラー(self):
        with pytest.raises(ValueError, match="フォーマットを入力してください"):
            parse_format("")

    def test_不正なフォーマットでエラー(self):
        with pytest.raises(ValueError, match="フォーマットを正しく入力してください"):
            parse_format("商品名, 価格")

    def test_列なしフォーマットでエラー(self):
        with pytest.raises(ValueError):
            parse_format("   ")

    def test_多列フォーマット(self):
        fmt = "A列: 商品名, B列: 価格, C列: 生産者, D列: 出荷日, E列: JANコード, F列: 処理日"
        result = parse_format(fmt)
        assert len(result["columns"]) == 6
        assert len(result["llm_fields"]) == 5  # 処理日はシステム補完
        assert "F" in result["system_fields"]


class TestBuildRow:
    def test_基本的な行データ生成(self):
        format_info = {
            "columns": {"A": "商品名", "B": "価格", "C": "生産者"},
            "llm_fields": ["商品名", "価格", "生産者"],
            "system_fields": {},
        }
        llm_result = {"商品名": "キャベツ", "価格": "380", "生産者": "山下 太郎"}
        row = build_row(llm_result, format_info)
        assert row == ["キャベツ", "380", "山下 太郎"]

    def test_nullは空文字に変換される(self):
        format_info = {
            "columns": {"A": "商品名", "B": "価格"},
            "llm_fields": ["商品名", "価格"],
            "system_fields": {},
        }
        llm_result = {"商品名": "キャベツ", "価格": None}
        row = build_row(llm_result, format_info)
        assert row == ["キャベツ", ""]

    def test_システム補完値が挿入される(self):
        format_info = {
            "columns": {"A": "商品名", "B": "処理日"},
            "llm_fields": ["商品名"],
            "system_fields": {"B": ("処理日", "2026/04/01")},
        }
        llm_result = {"商品名": "キャベツ"}
        row = build_row(llm_result, format_info)
        assert row == ["キャベツ", "2026/04/01"]

    def test_列がアルファベット順にソートされる(self):
        format_info = {
            "columns": {"C": "生産者", "A": "商品名", "B": "価格"},
            "llm_fields": ["商品名", "価格", "生産者"],
            "system_fields": {},
        }
        llm_result = {"商品名": "キャベツ", "価格": "380", "生産者": "山下"}
        row = build_row(llm_result, format_info)
        assert row == ["キャベツ", "380", "山下"]

    def test_LLMに存在しないフィールドは空文字(self):
        format_info = {
            "columns": {"A": "商品名", "B": "JANコード"},
            "llm_fields": ["商品名", "JANコード"],
            "system_fields": {},
        }
        llm_result = {"商品名": "キャベツ"}  # JANコードなし
        row = build_row(llm_result, format_info)
        assert row == ["キャベツ", ""]
