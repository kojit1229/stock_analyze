"""依存関係ゼロの最小 PDF ジェネレータ。

決算短信の実データ取得はモック（fetcher.py）で行うため、閲覧機能を成立させる
サンプル PDF をここで生成する。標準の Helvetica フォントを使い、ASCII テキストの
1ページ PDF を組み立てる。日本語フォント埋め込みは行わないため、本文はローマ字/
英数字で表現する（MVP のサンプル用途）。
"""


def _escape(text):
    """PDF 文字列リテラル用にエスケープする。"""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_pdf(lines):
    """テキスト行のリストから 1 ページの PDF バイト列を生成する。

    lines: 各行の文字列(ASCII 推奨)。先頭行は見出しとして大きめに描画する。
    """
    # --- ページ内容ストリーム(テキスト描画命令) ---
    content_parts = ["BT", "/F1 18 Tf", "1 0 0 1 60 760 Tm", "20 TL"]
    first = True
    for line in lines:
        safe = _escape(str(line))
        if first:
            content_parts.append(f"({safe}) Tj")
            content_parts.append("/F1 11 Tf")
            content_parts.append("16 TL")
            content_parts.append("T*")
            first = False
        else:
            content_parts.append(f"({safe}) Tj")
            content_parts.append("T*")
    content_parts.append("ET")
    content = "\n".join(content_parts).encode("latin-1", "replace")

    # --- PDF オブジェクト群 ---
    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    )
    objects.append(
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content + b"\nendstream"
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # --- ファイル本体を組み立て、相互参照テーブルを作る ---
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]  # オブジェクト0はフリーエントリ
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode("ascii") + b" 0 obj\n" + obj + b"\nendobj\n"

    xref_pos = len(out)
    n = len(objects) + 1
    out += b"xref\n"
    out += b"0 " + str(n).encode("ascii") + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += ("%010d 00000 n \n" % off).encode("ascii")
    out += b"trailer\n"
    out += b"<< /Size " + str(n).encode("ascii") + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_pos).encode("ascii") + b"\n%%EOF\n"
    return bytes(out)
