# RealmStatus

Telegram-бот для Minecraft, Terraria и Stardew Valley. обновляет одно сообщение с онлайном, адресами серверов и последней активностью игроков. через команды можно запускать и останавливать настроенные серверы.

## как запустить на Mac

нужен Python 3.12 или новее

```sh
cp .env.example .env
cp games.example.toml games.toml
make setup
```

в `.env` укажи `TELEGRAM_API_ID` и `TELEGRAM_API_HASH` из настроек приложения Telegram, а `TELEGRAM_BOT_TOKEN` возьми у BotFather. [настройки приложения Telegram](https://my.telegram.org/apps)

в `games.toml` подставь свой чат и ID сообщения, которое бот будет обновлять. бот должен быть автором этого сообщения или иметь право редактировать посты в канале. если он ещё не видит закрытую группу, отправь в ней `/status@имя_бота` после запуска

```sh
make preview
make start
```

`make preview` покажет статус в терминале без подключения к Telegram. `make start` запустит бота в фоне, лог будет в `.state/bot.log`. чтобы остановить бота, используй `make stop`. чтобы оставить его в текущем терминале, используй `make run`

## что настроить под себя

все поля с комментариями есть в [шаблоне настроек](games.example.toml). рабочие значения храни в локальных `games.toml` и `.env`

- в `[telegram]` настрой, куда публиковать статус, как часто его обновлять и кому доступны команды. в личке команды доступны пользователям из `allowed_user_ids`. в группах бот проверяет `control_chat_ids` и список пользователей. `allow_all_users` открывает команды всем участникам указанных групп
- `show_offline_players = false` скрывает вышедших игроков, история при этом продолжает копиться. `center_server_headers = true` центрирует адрес, порты и онлайн. название сервера всегда по центру
- в `[games]` можно выключить любую игру или поменять её `source`. `host` и `port` нужны для опроса, а `public_host`, `public_port` и `public_bedrock_port` бот показывает игрокам
- если хочешь запускать сервер из Telegram, добавь в его источник блок `control` с папкой проекта и Make-целями. для FRPC есть свой блок `[tunnel]`, его можно выключить отдельно

после правок в конфиге перезапусти бота через `make stop` и `make start`. другой путь к TOML можно задать через `GAMES_CONFIG`

## команды в Telegram

- `/status` сразу обновляет сообщение
- `/minecraft_start` и `/minecraft_stop` управляют сервером Minecraft
- `/terraria_start` и `/terraria_stop` управляют tModLoader
- `/tunnel_start`, `/tunnel_stop` и `/tunnel_restart` управляют FRPC
- `/emoji_sync` обновляет набор эмодзи и возвращает ссылку на него

имена команд игры берутся из её ID в конфиге. если серверная Make-цель открывает Terminal, на Mac нужна активная пользовательская сессия

## головы игроков и эмодзи

рядом с названием игры бот ставит её иконку, а рядом с ником игрока – его голову, когда портрет доступен. Minecraft берёт скины из SkinsRestorer, а для известных игроков без локального скина пробует Minotar для Java и Geyser Global API для Bedrock. головы Terraria приходят из tMapOnline. бот хранит портреты локально и проверяет изменения раз в минуту по умолчанию

чтобы включить набор, заполни `[emoji_pack]` в `games.toml` и отправь боту `/start` с аккаунта владельца набора. при запуске бот найдёт существующий набор или создаст новый. `make emoji-prepare` собирает PNG локально. [откуда берутся картинки](assets/emoji/README.md)

## если сервер другой

для локального Minecraft выбери `minecraft_java`. Terraria получает список игроков через `terraria_viewer`, для этого нужен запущенный tMapOnline с телеметрией

для Minecraft Realms выбери `minecraft_realms`, укажи свой `world_id` и заполни `MYSQL_*` в `.env`. затем выполни `make setup-realms` и, если нужно заново войти в Microsoft, `make realms-login`. когда миров несколько, можно указать ID игры через `.venv/bin/python -m realm_status --realms-login <id>`

для Stardew Valley включи игру, выбери `stardew_api` и заполни адрес API и `STARDEW_API_TOKEN`

старый облачный запуск через `main.py` остался в проекте. для одного сообщения запускай один процесс публикации. [как устроен бот](docs/architecture.md)
