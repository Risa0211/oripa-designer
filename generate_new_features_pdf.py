"""NEW_FEATURES_2026-06.md → PDF（簡潔版・最新追加機能のみ）"""
from pathlib import Path
import subprocess
import tempfile
import markdown

ROOT = Path(__file__).parent
md_path = ROOT / "NEW_FEATURES_2026-06.md"
pdf_path = ROOT / "NEW_FEATURES_2026-06.pdf"
logo_wide = (ROOT / "assets/logo_wide.png").absolute().as_uri()

md_text = md_path.read_text(encoding="utf-8")
html_body = markdown.markdown(
    md_text,
    extensions=["tables", "fenced_code", "codehilite", "toc"],
)

CSS = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
body {
    font-family: "Hiragino Sans", "Hiragino Kaku Gothic Pro", "Yu Gothic", sans-serif;
    font-size: 10.5pt; line-height: 1.7; color: #1a1a1a; margin: 0;
}
h1 {
    color: #1a1a1a; border-bottom: 4px solid #F5B800;
    padding-bottom: 8px; padding-top: 8px; font-size: 20pt;
    page-break-before: always; margin-top: 0;
}
h1:first-of-type { page-break-before: avoid; }
h2 {
    color: #1a1a1a; border-left: 5px solid #F5B800;
    padding-left: 12px; margin-top: 22px; font-size: 14pt;
}
h3 { color: #333; margin-top: 14px; font-size: 12pt; }
p, li { font-size: 10.5pt; }
ul, ol { padding-left: 22px; }
code {
    font-family: "Menlo", "Courier", monospace;
    background: #f5f5f5; padding: 2px 5px; border-radius: 3px; font-size: 9.5pt;
}
pre {
    background: #2d2d2d; color: #f0f0f0; padding: 12px; border-radius: 6px;
    overflow-x: auto; font-size: 9pt; page-break-inside: avoid; white-space: pre-wrap;
}
pre code { background: transparent; color: inherit; padding: 0; }
blockquote {
    border-left: 4px solid #F5B800; margin: 10px 0; padding: 8px 16px;
    background: #fff9e6; color: #555;
}
table {
    border-collapse: collapse; width: 100%; margin: 10px 0;
    font-size: 10pt; page-break-inside: avoid;
}
th, td {
    border: 1px solid #ddd; padding: 7px 9px; text-align: left; vertical-align: top;
}
th { background: #F5B800; color: #1a1a1a; font-weight: 600; }
tr:nth-child(even) td { background: #fafafa; }
hr { border: none; border-top: 1px dashed #ccc; margin: 20px 0; }
a { color: #c79400; text-decoration: none; }
.cover {
    text-align: center; padding: 100px 0 60px; page-break-after: always;
}
.cover img { max-width: 320px; margin-bottom: 40px; }
.cover-title { font-size: 24pt; font-weight: 700; color: #1a1a1a; margin: 20px 0 12px; }
.cover-sub { font-size: 13pt; color: #666; }
.cover-ver { margin-top: 100px; font-size: 11pt; color: #999; }
.cover-badge {
    display: inline-block; background: #F5B800; color: #1a1a1a;
    padding: 6px 18px; border-radius: 6px; font-size: 12pt; font-weight: 600;
    margin-top: 8px;
}
"""

today = "2026-06-02"
full_html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>みんなのトレカ オリパ商品設計ツール 2026年6月 追加機能ガイド</title>
<style>{CSS}</style>
</head>
<body>
<div class="cover">
    <img src="{logo_wide}" alt="みんなのトレカ">
    <div class="cover-title">オリパ商品設計ツール</div>
    <div class="cover-sub">2026年6月 追加機能ガイド</div>
    <div class="cover-badge">変更点だけサクッと把握</div>
    <div class="cover-ver">{today}</div>
</div>
{html_body}
</body>
</html>
"""

with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as f:
    f.write(full_html)
    html_tmp = Path(f.name)

chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
cmd = [
    chrome_path, "--headless=new", "--disable-gpu", "--no-sandbox",
    "--print-to-pdf-no-header", f"--print-to-pdf={pdf_path}",
    html_tmp.as_uri(),
]
result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
html_tmp.unlink(missing_ok=True)

if result.returncode != 0:
    print("STDERR:", result.stderr)
    raise SystemExit(f"Chrome PDF生成失敗: exit {result.returncode}")

print(f"✅ PDF生成完了: {pdf_path}")
print(f"   ({pdf_path.stat().st_size / 1024:.1f} KB)")
