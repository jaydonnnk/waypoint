# Waypoint frontend — Next.js 15 deployment image (S10, ADR 0007).
#
# NEXT_PUBLIC_API_URL is baked at BUILD time (Next inlines NEXT_PUBLIC_*
# into the client bundle): the browser talks to the backend directly
# (SSE + REST), so the value must be an origin the VIEWER's browser can
# reach — http://localhost:8000 for the local compose deployment.
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
# Waybot gate (S3, M8): "false" seeds desks UNGATED so the recorded/scripted
# pitch books demo pax byte-safe (no bot token, no traveler capture). Any
# other value (default) keeps the gated G1 flow for the live bot demo.
ARG NEXT_PUBLIC_WAYBOT_GATED=true
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_WAYBOT_GATED=$NEXT_PUBLIC_WAYBOT_GATED \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_STANDALONE=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Next standalone output: server.js + the minimal node_modules it needs.
# Static assets are not included in the standalone bundle — copy them in.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
