FROM mirror.gcr.io/library/golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o /server ./cmd/server

FROM mirror.gcr.io/library/alpine:3.21
RUN addgroup -g 10001 -S app && adduser -u 10001 -S app -G app && mkdir -p /data/uploads && chown -R app:app /data
WORKDIR /app
COPY --from=build /server /app/server
COPY --from=build /src/migrations /app/migrations
USER app
EXPOSE 8080
ENTRYPOINT ["/app/server"]
