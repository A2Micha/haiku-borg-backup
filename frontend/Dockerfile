FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json tsconfig.json vite.config.ts index.html ./
RUN npm ci --no-audit --no-fund
COPY src ./src
RUN npm run build
FROM nginx:1.27-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
