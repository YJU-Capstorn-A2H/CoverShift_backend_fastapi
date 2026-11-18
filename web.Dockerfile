# syntax=docker/dockerfile:1.7
# 관리자 · 직원 화면 (D-1). web/ 를 빌드해 nginx 로 내보내고 /api 는 입구 API(api:8010)로 넘긴다
# (개발 때 Vite 가 /api 를 127.0.0.1:8010 으로 넘기는 것과 같은 길, API_CONTRACT.md 「공통」).
# (가정 신규) web/ 는 npm 프로젝트이고 `npm run build` 가 web/dist 를 만든다. package-lock.json 이 있으면 npm ci.

FROM node:22.21.1-alpine3.22 AS build
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install --no-audit --no-fund; fi
COPY web/ ./
RUN npm run build

FROM nginx:1.28.0-alpine3.21
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen 80;
    root /usr/share/nginx/html;
    location /api/ {
        proxy_pass http://api:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
    location / {
        try_files $uri /index.html;
    }
}
EOF
COPY --from=build /web/dist /usr/share/nginx/html
