# --- stage 1: build the frontend ---
FROM node:20-slim AS frontend-build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html vite.config.ts tsconfig*.json ./
COPY src ./src
COPY public ./public
RUN npm run build

# --- stage 2: python runtime ---
FROM python:3.12-slim AS runtime
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server ./server
COPY --from=frontend-build /app/dist ./dist
ENV PORT=8787
EXPOSE 8787
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8787"]
