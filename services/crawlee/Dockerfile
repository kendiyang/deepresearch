# 使用带有 Playwright 依赖的官方 Node 镜像
FROM apify/actor-node-playwright-chrome:20

WORKDIR /usr/src/app

COPY package*.json ./
RUN npm install --only=production

COPY . .
RUN npm run build

# 关键：设置持久化存储路径
ENV CRAWLEE_STORAGE_DIR=/data/crawlee_storage
VOLUME /data/crawlee_storage

CMD ["node", "dist/main.js"]