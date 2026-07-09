FROM node:20-slim

WORKDIR /workspace
RUN chown node:node /workspace

USER node:node

CMD ["node", "-e", "setTimeout(() => {}, 3600000)"]