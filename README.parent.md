# MCP Server Deployment

Этот проект поднимает MCP Server с поддержкой HTTPS через [Caddy](https://caddyserver.com).  
Стек: Docker Compose + Caddy (reverse proxy) + Uvicorn/FastAPI приложение.

---

## Структура проекта

```
.
├── Dockerfile                # Образ приложения (uvicorn + FastAPI)
├── docker-compose.yaml       # Основной compose для продакшн-развертывания (HTTPS)
├── docker-compose.local.yaml # Локальный compose для разработки (self-signed TLS)
├── deploy/
│   ├── Caddyfile             # Конфиг Caddy для продакшн (Let's Encrypt/ZeroSSL)
│   └── Caddyfile.local       # Конфиг Caddy для локальной разработки (tls internal)
└── .env                      # Переменные окружения (DOMAIN, ACME_EMAIL и др.)
```

---

## Переменные окружения

Создайте файл `.env` в корне проекта:

```env
DOMAIN=your.domain.com
ACME_EMAIL=your@email.com
```

- `DOMAIN` — доменное имя, которое указывает на ваш сервер.  
- `ACME_EMAIL` — e-mail для регистрации сертификата и уведомлений.

---

## Локальный запуск (self-signed HTTPS)

Для разработки можно использовать встроенные сертификаты Caddy:

```bash
docker compose -f docker-compose.local.yaml up -d --build
```

После этого приложение будет доступно по адресу:

```
https://localhost/health
```

⚠️ Браузер может показать предупреждение о недоверенном сертификате.

---

## Продакшн-развертывание (с реальным доменом)

1. Убедитесь, что DNS-домен указывает на IP вашего сервера.  
2. Откройте порты 80 и 443 в фаерволе сервера и панели хостинга.  
3. Запустите:

```bash
docker compose up -d --build
```

Caddy автоматически получит TLS-сертификат и настроит HTTPS-доступ.

Проверка:

```bash
curl -vk https://your.domain.com/health
```

Ожидаемый результат:

```
HTTP/2 200
{"status": "ok"}
```

---

## Управление контейнерами

Остановить сервисы:

```bash
docker compose down
```

Перезапустить только Caddy:

```bash
docker compose restart caddy
```

Посмотреть логи Caddy:

```bash
docker logs -f caddy
```

---

## Автоматическое продление сертификатов

Caddy автоматически продлевает сертификаты за ~30 дней до истечения.  
Никаких дополнительных cron-задач не требуется.

---

## Примечания

- Для проверки корректности конфигурации Caddy можно выполнить:

```bash
docker exec -it caddy caddy validate --config /etc/caddy/Caddyfile
```

- В локальной разработке можно использовать [`mkcert`](https://github.com/FiloSottile/mkcert) для доверенных локальных сертификатов.
