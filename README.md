# Olga Job Search Bot

Telegram бот который мониторит job boards и присылает только подходящие вакансии для Senior Product Designer.

## Деплой на Railway

### 1. Получи свой Telegram Chat ID
Напиши боту @userinfobot в Telegram — он пришлёт твой Chat ID (число вида 123456789).

### 2. Залей код на GitHub
- Создай новый приватный репозиторий на github.com
- Загрузи все файлы из этой папки

### 3. Задеплой на Railway
- Иди на railway.app, войди через GitHub
- New Project → Deploy from GitHub repo → выбери репозиторий
- После деплоя иди в Variables и добавь три переменные:

```
TELEGRAM_TOKEN = токен от BotFather (выглядит как 1234567890:ABCdef...)
TELEGRAM_CHAT_ID = твой Chat ID из userinfobot
ANTHROPIC_API_KEY = твой Anthropic API ключ
```

### 4. Готово
Бот запустится, сразу проверит вакансии и будет проверять каждые 4 часа.

## Источники (RSS)
- Wellfound Netherlands
- RemoteOK product designer
- We Work Remotely design

## Что присылает
- 🟢 STRONG — очень подходящая вакансия
- 🟡 MEDIUM — стоит посмотреть
- 🔴 WEAK — product design но не идеально

Роли с Dutch required, junior, graphic/marketing design — не присылает вообще.
