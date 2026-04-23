"""USER_GUIDE.md → PDF（日本語対応・みんなのトレカブランディング）
ヘッドレスChromeでHTMLをPDF化"""
from pathlib import Path
import subprocess
import tempfile
import markdown

ROOT = Path(__file__).parent
md_path = ROOT / "USER_GUIDE.md"
pdf_path = ROOT / "USER_GUIDE.pdf"
logo_wide = (ROOT / "assets/logo_wide.png").absolute().as_uri()

md_text = md_path.read_text(encoding="utf-8")
html_body = markdown.markdown(
    md_text,
    extensions=["tables", "fenced_code", "codehilite", "toc"],
)

CSS = """
@page {
    size: A4;
    margin: 20mm 18mm 22mm 18mm;
}
body {
    font-family: "Hiragino Sans", "Hiragino Kaku Gothic Pro", "Yu Gothic", sans-serif;
    font-size: 10.5pt;
    line-height: 1.75;
    color: #1a1a1a;
    margin: 0;
}
h1 {
    color: #1a1a1a;
    border-bottom: 4px solid #F5B800;
    padding-bottom: 10px;
    padding-top: 12px;
    font-size: 22pt;
    page-break-before: always;
    margin-top: 0;
}
h1:first-of-type { page-break-before: avoid; }
h2 {
    color: #1a1a1a;
    border-left: 6px solid #F5B800;
    padding-left: 14px;
    margin-top: 26px;
    font-size: 16pt;
}
h3 { color: #333; margin-top: 18px; font-size: 13pt; }
h4 { color: #444; font-size: 11pt; }
p, li { font-size: 10.5pt; }
ul, ol { padding-left: 24px; }
code {
    font-family: "Menlo", "Courier", monospace;
    background: #f5f5f5;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 9.5pt;
}
pre {
    background: #2d2d2d;
    color: #f0f0f0;
    padding: 14px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 9pt;
    page-break-inside: avoid;
    white-space: pre-wrap;
}
pre code { background: transparent; color: inherit; padding: 0; }
blockquote {
    border-left: 4px solid #F5B800;
    margin: 12px 0;
    padding: 10px 18px;
    background: #fff9e6;
    color: #555;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
    font-size: 10pt;
    page-break-inside: avoid;
}
th, td {
    border: 1px solid #ddd;
    padding: 8px 10px;
    text-align: left;
    vertical-align: top;
}
th { background: #F5B800; color: #1a1a1a; font-weight: 600; }
tr:nth-child(even) td { background: #fafafa; }
hr { border: none; border-top: 1px dashed #ccc; margin: 24px 0; }
a { color: #c79400; text-decoration: none; }
.cover {
    text-align: center;
    padding: 100px 0 60px;
    page-break-after: always;
}
.cover img { max-width: 320px; margin-bottom: 40px; }
.cover-title {
    font-size: 28pt; font-weight: 700; color: #1a1a1a; margin: 20px 0 12px;
}
.cover-sub { font-size: 14pt; color: #666; }
.cover-ver { margin-top: 100px; font-size: 11pt; color: #999; }
"""

full_html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>みんなのトレカ オリパ商品設計ツール 使い方ガイド</title>
<style>{CSS}</style>
</head>
<body>
<div class="cover">
    <img src="{logo_wide}" alt="みんなのトレカ">
    <div class="cover-title">オリパ商品設計ツール</div>
    <div class="cover-sub">使い方ガイド</div>
    <div class="cover-ver">v1.0  |  2026-04-23</div>
</div>
{html_body}
</body>
</html>
"""

# HTMLを一時ファイルに書出
with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
    f.write(full_html)
    html_tmp = Path(f.name)

# ChromeでHTML→PDF変換
chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
cmd = [
    chrome_path,
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--print-to-pdf-no-header",
    f"--print-to-pdf={pdf_path}",
    html_tmp.as_uri(),
]
result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
html_tmp.unlink(missing_ok=True)

if result.returncode != 0:
    print("STDERR:", result.stderr)
    raise SystemExit(f"Chrome PDF生成失敗: exit {result.returncode}")

print(f"✅ PDF生成完了: {pdf_path}")
print(f"   ({pdf_path.stat().st_size / 1024:.1f} KB)")
