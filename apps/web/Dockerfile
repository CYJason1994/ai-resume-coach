FROM node:20-alpine

WORKDIR /app
ENV NEXT_PUBLIC_API_URL=http://api:3001

COPY package.json ./
RUN npm install
COPY . .

EXPOSE 3000
CMD ["npm", "run", "dev"]
