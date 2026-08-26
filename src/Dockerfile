# 軽量なNode.jsイメージを使用
FROM node:22-alpine

# アプリケーションディレクトリを作成
WORKDIR /usr/src/app

# 依存関係のファイルをコピーしてインストール
COPY package*.json ./
RUN apk add --no-cache --virtual .build-deps python3 make g++ \
	&& npm install --omit=dev \
	&& apk del .build-deps

# ソースコードをコピー
COPY . .

# Renderが指定するポートを開放
EXPOSE 3000

# Node.jsをPID 1として起動し、Renderの終了シグナルを直接受け取る
CMD [ "node", "src/server.js" ]