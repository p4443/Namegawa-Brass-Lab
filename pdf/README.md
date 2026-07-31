このフォルダに PDF ファイルを配置してください。

ファイル名の例:
- dayservice.pdf
- shukatsu.pdf
- gakudou.pdf
- hoikuen.pdf

ブラウザで動作させる際はファイル名を正確に（大文字小文字も含めて）してください。

テスト用に空のPDFを作成するには:

macOS:
```
# 空のPDFを作成（1ページ、A4相当）
qpdf --empty --pages /dev/null -- pdf.pdf || true
# または単純にテキストをPDFに変換する方法を使用してください。
```

注意: 実運用では実際のPDFを配置してください。