FROM node:22-alpine AS build
WORKDIR /web
COPY package.json tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm install && npm run build
FROM nginx:1.27-alpine
COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
