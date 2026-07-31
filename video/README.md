このフォルダにオープニング動画 `intro.mp4` を置いてください。

推奨設定：
- ファイル名: intro.mp4
- コンテナ: MP4
- ビデオコーデック: H.264（AVC）
- オーディオコーデック: AAC
- 解像度: 1280x720（HD）または1920x1080（Full HD）
- 推奨ビットレート: 2000-5000 kbps（画質に応じて調整）

アップロード方法の例：
- Finder でコピー／貼り付け
- ターミナル（scp の例）:
  scp /path/to/intro.mp4 user@host:/path/to/hp/video/intro.mp4
- ローカルテスト用に簡易サーバーを使う場合（`index.html` と同じ階層で）:
  python3 -m http.server 8000
  -> ブラウザで http://localhost:8000/ を開く

注意: `index.html` は `video/intro.mp4` を参照します。ファイル名や大文字小文字を正確に揃えてください。